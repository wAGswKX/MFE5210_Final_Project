# up_amount_ratio_timing.py
# 单因子择时：up_amount_ratio（上涨股票成交额占比）
# 逻辑：MA60 平滑后，与 20 日前比较，上升做多，下降做空
# 回测：日频，收益按指数收盘涨跌；交易成本含滑点与手续费

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# =========================
# 0) 可调参数区（你只改这里）
# =========================
INDEX_FILE = "中证全指数据2.xlsx"          # 图三那份指数数据（含 date, close, 总市值, vol 等）
FACTOR_FILE = "a_share_up_amount_ratio.csv"  # 因子数据（含 trade_date, up_ratio）

DATE_COL_INDEX = "date"     # 指数文件日期列名（图三：date）
CLOSE_COL = "close"         # 指数收盘价列名（图三：close）

DATE_COL_FACTOR = "trade_date"  # 因子文件日期列名（图四：trade_date）
FACTOR_COL = "up_ratio"         # 因子列名（图四：up_ratio）

START_DATE = "2016-12-31"   # 回测起始（可调）
END_DATE = "2026-01-21"     # 回测结束（可调）

SMOOTH_WIN = 60             # 平滑窗口（图一：60天）
COMPARE_LAG = 20            # 与多少天前比较（图二：20天前）
SIGNAL_LAG = 1              # 信号执行延迟：1=次日执行（推荐）；0=同日执行（更贴近PPT）

FEE_RATE = 0.0003           # 单边手续费（万三=0.0003）
SLIPPAGE = 0.0000           # 单边滑点（比如 2bp=0.0002）。若你想“总滑点”，就这里填单边即可
RISK_FREE = 0.0             # 无风险利率（年化，通常可设0）

ANNUAL_TRADING_DAYS = 252   # 年化因子


# =========================
# 1) 工具函数
# =========================
def parse_yyyymmdd(s):
    """兼容 int(20160104) / str('20160104') / 'YYYY-MM-DD' / Timestamp；不可解析则返回 NaT"""
    if pd.isna(s):
        return pd.NaT

    # already datetime-like
    if isinstance(s, (pd.Timestamp, np.datetime64)):
        try:
            return pd.to_datetime(s)
        except Exception:
            return pd.NaT

    # numbers (including excel-like floats)
    if isinstance(s, (int, np.integer)):
        s = str(s)
    elif isinstance(s, (float, np.floating)):
        # e.g. 20160104.0
        if np.isfinite(s):
            s = str(int(s))
        else:
            return pd.NaT
    else:
        s = str(s)

    s = s.strip()
    if s == "":
        return pd.NaT

    # common garbage like "数据来源：Wind" or other footers/headers
    # keep only typical date patterns
    try:
        if s.isdigit() and len(s) == 8:
            return pd.to_datetime(s, format="%Y%m%d")
        # allow YYYY-MM-DD
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return pd.to_datetime(s, format="%Y-%m-%d")
        # allow YYYY/MM/DD
        if len(s) == 10 and s[4] == "/" and s[7] == "/":
            return pd.to_datetime(s, format="%Y/%m/%d")
        # fallback parser (may still fail on non-date strings)
        return pd.to_datetime(s, errors="coerce")
    except Exception:
        return pd.NaT


def max_drawdown(equity_curve: pd.Series):
    """返回最大回撤（正数，比如 0.25 表示回撤25%）"""
    peak = equity_curve.cummax()
    dd = equity_curve / peak - 1.0
    return float((-dd.min()) if len(dd) else 0.0)


def perf_stats(daily_ret: pd.Series):
    daily_ret = daily_ret.dropna()
    if daily_ret.empty:
        return {}

    eq = (1 + daily_ret).cumprod()
    ann_ret = eq.iloc[-1] ** (ANNUAL_TRADING_DAYS / len(daily_ret)) - 1
    ann_vol = daily_ret.std(ddof=0) * np.sqrt(ANNUAL_TRADING_DAYS)
    sharpe = (ann_ret - RISK_FREE) / ann_vol if ann_vol > 0 else np.nan

    mdd = max_drawdown(eq)
    calmar = ann_ret / mdd if mdd > 0 else np.nan

    win_rate = (daily_ret > 0).mean()

    wins = daily_ret[daily_ret > 0]
    losses = daily_ret[daily_ret < 0]
    odds = (wins.mean() / (-losses.mean())) if (len(wins) > 0 and len(losses) > 0) else np.nan

    return {
        "年化收益率": ann_ret,
        "年化波动率": ann_vol,
        "夏普比率": sharpe,
        "最大回撤": mdd,
        "收益回撤比": calmar,
        "胜率": win_rate,
        "赔率": odds,
        "累计收益率": eq.iloc[-1] - 1,
    }


# =========================
# 2) 读数据 & 对齐
# =========================
# 指数数据：Excel
idx = pd.read_excel(INDEX_FILE)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].astype(str)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].apply(parse_yyyymmdd)
idx = idx.dropna(subset=[DATE_COL_INDEX])
idx = idx.sort_values(DATE_COL_INDEX)
idx = idx[[DATE_COL_INDEX, CLOSE_COL]].rename(columns={DATE_COL_INDEX: "date", CLOSE_COL: "close"})

# 因子数据：CSV
fac = pd.read_csv(FACTOR_FILE)
fac[DATE_COL_FACTOR] = fac[DATE_COL_FACTOR].apply(parse_yyyymmdd)
fac = fac.dropna(subset=[DATE_COL_FACTOR]).sort_values(DATE_COL_FACTOR)
fac = fac[[DATE_COL_FACTOR, FACTOR_COL]].rename(columns={DATE_COL_FACTOR: "date", FACTOR_COL: "up_ratio"})

# 合并
df = pd.merge(idx, fac, on="date", how="inner")
df = df[(df["date"] >= pd.to_datetime(START_DATE)) & (df["date"] <= pd.to_datetime(END_DATE))].copy()
df = df.sort_values("date").reset_index(drop=True)

# 计算指数日收益（收盘到收盘）
df["ret"] = df["close"].pct_change()


# =========================
# 3) 因子处理 & 信号生成
# =========================
# 60日平滑
df["factor_ma"] = df["up_ratio"].rolling(SMOOTH_WIN, min_periods=SMOOTH_WIN).mean()

# 与20日前比较：上升=做多；下降=做空；相等=空仓(0)
df["factor_prev"] = df["factor_ma"].shift(COMPARE_LAG)
df["signal_raw"] = np.where(df["factor_ma"] > df["factor_prev"], 1,
                    np.where(df["factor_ma"] < df["factor_prev"], -1, 0))

# 信号执行延迟（避免同日偷看未来）
df["pos"] = df["signal_raw"].shift(SIGNAL_LAG).fillna(0).astype(int)

# =========================
# 4) 交易成本 & 策略收益
# =========================
# 仓位变化决定换手：pos从-1到+1差2，意味着两次交易（平空+开多）
df["pos_prev"] = df["pos"].shift(1).fillna(0).astype(int)
df["turnover_units"] = (df["pos"] - df["pos_prev"]).abs()  # 0/1/2

# 单边成本=手续费+滑点；一次“单位交易”成本按 notional 计
one_side_cost = FEE_RATE + SLIPPAGE
df["cost"] = df["turnover_units"] * one_side_cost

# 策略日收益：用昨日持仓吃到今日指数涨跌（pos_prev），再扣成本
df["strat_ret_gross"] = df["pos_prev"] * df["ret"]
df["strat_ret"] = df["strat_ret_gross"] - df["cost"]

# 净值曲线
df["index_nav"] = (1 + df["ret"].fillna(0)).cumprod()
df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()


# =========================
# 5) 绩效输出
# =========================
stats = perf_stats(df["strat_ret"])
print("\n===== 策略绩效（净） =====")
for k, v in stats.items():
    if isinstance(v, (float, np.floating)) and np.isfinite(v):
        if "率" in k or "回撤" in k:
            print(f"{k}: {v:.2%}")
        else:
            print(f"{k}: {v:.4f}")
    else:
        print(f"{k}: {v}")

# =========================
# 6) 画图：图一（MA60 vs 指数）
# =========================
plt.figure(figsize=(12, 5))
ax1 = plt.gca()
ax1.plot(df["date"], df["factor_ma"], label=f"up_ratio_MA{SMOOTH_WIN}（左轴）")
ax1.set_ylabel("up_ratio_MA")

ax2 = ax1.twinx()
ax2.plot(df["date"], df["close"], alpha=0.6, label="中证全指（右轴）")
ax2.set_ylabel("Index Close")

# 合并图例
lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.title(f"up_ratio_MA{SMOOTH_WIN} vs 中证全指走势")
plt.tight_layout()
plt.show()


# =========================
# 7) 画图：图二（策略净值 vs 指数 + 持仓背景）
# =========================
plt.figure(figsize=(12, 5))
ax = plt.gca()

# 背景持仓：多头浅灰、空头深灰（你也可以改颜色或透明度）
# 这里不指定具体颜色也能看，但默认颜色会比较花；为了清晰，我只用灰度。
long_mask = df["pos_prev"] == 1
short_mask = df["pos_prev"] == -1

# 用 fill_between 做背景条
ax.fill_between(df["date"], 0, 1, where=long_mask, transform=ax.get_xaxis_transform(),
                alpha=0.12, label="做多区间")
ax.fill_between(df["date"], 0, 1, where=short_mask, transform=ax.get_xaxis_transform(),
                alpha=0.25, label="做空区间")

# 净值曲线（右轴净值更直观）
ax2 = ax.twinx()
ax2.plot(df["date"], df["index_nav"], label="中证全指净值（右轴）", linewidth=1.5)
ax2.plot(df["date"], df["strat_nav"], label="策略净值（右轴）", linewidth=1.8)

ax.set_title(f"up_ratio_MA{SMOOTH_WIN}-trend 策略（compare_lag={COMPARE_LAG}, signal_lag={SIGNAL_LAG}）")
ax.set_ylabel("持仓区间（背景）")
ax2.set_ylabel("净值")

# 合并图例
lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.tight_layout()
plt.show()


# =========================
# 8) 可选：导出结果
# =========================
out = df[["date", "close", "up_ratio", "factor_ma", "signal_raw", "pos", "ret", "strat_ret", "index_nav", "strat_nav"]].copy()
out.to_csv("up_amount_ratio_timing_result.csv", index=False, encoding="utf-8-sig")
print("\n已导出：up_amount_ratio_timing_result.csv")
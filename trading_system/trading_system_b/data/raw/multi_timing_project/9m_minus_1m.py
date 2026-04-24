# iv_term_9m_minus_1m_timing.py
# 单因子择时：期权波动率曲面（期限结构）
# 因子：9m_minus_1m = ATM IV(9m) - ATM IV(1m)
# 策略（按PPT图二）：MA5平滑后，对比20日前；上升->做空，下降->做多（方向=-1）
# 交易执行价可切换：次日开盘 or 次日收盘

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["PingFang SC", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# =========================
# 0) 可调参数区（你只改这里）
# =========================
INDEX_FILE = "中证全指数据2.xlsx"

FACTOR_FILE = "50etf_平值期权IV（9m）-平值期权IV（1m）.xlsx"
FACTOR_SHEET = 0

# 图四里日期列是 date（且像 20220505 这种），如果你的实际列名是“日期”，改成 "日期"
DATE_COL_FACTOR = "date"
FACTOR_COL = "9M_minus_1M"

DATE_COL_INDEX = "date"
OPEN_COL = "open"
CLOSE_COL = "close"

START_DATE = "2022-01-01"
END_DATE = "2026-01-22"

MA_WIN = 5            # 图二：5日平滑
COMPARE_LAG = 20      # 图二：对比20日前（shift_n=20）
SIGNAL_LAG = 1        # 信号滞后：1=次日执行（推荐），0=同日执行（偏理想化）
MIN_HOLD_DAYS = 4     # 持仓天数n：0不限制；比如 2/3/5 可以降低换手

FEE_RATE = 0.0003     # 单边手续费
SLIPPAGE = 0.0000     # 单边滑点
ANNUAL_TRADING_DAYS = 252

# 执行价选择：
# - "open": 信号T收盘产生 -> T+1开盘成交 -> 当天收盘计价（更真实）
# - "close": 信号T收盘产生 -> T+1收盘成交 -> 收盘计价（close-to-close 简化）
EXECUTION_PRICE = "close"   # 改成 "close" 即可切换


# =========================
# 1) 工具函数
# =========================
def parse_date(s):
    if pd.isna(s):
        return pd.NaT
    if isinstance(s, (pd.Timestamp, np.datetime64)):
        return pd.to_datetime(s, errors="coerce")

    if isinstance(s, (int, np.integer)):
        n = int(s)
        if 19000101 <= n <= 21001231:
            return pd.to_datetime(str(n), format="%Y%m%d", errors="coerce")
        if 30000 <= n <= 60000:
            return pd.to_datetime(n, unit="D", origin="1899-12-30", errors="coerce")
        return pd.NaT

    if isinstance(s, (float, np.floating)):
        if not np.isfinite(s):
            return pd.NaT
        n = int(s)
        if 19000101 <= n <= 21001231:
            return pd.to_datetime(str(n), format="%Y%m%d", errors="coerce")
        if 30000 <= n <= 60000:
            return pd.to_datetime(n, unit="D", origin="1899-12-30", errors="coerce")
        return pd.NaT

    s = str(s).strip()
    if s.isdigit() and len(s) == 8:
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(s, errors="coerce")


def max_drawdown(eq: pd.Series) -> float:
    peak = eq.cummax()
    dd = eq / peak - 1.0
    return float((-dd.min()) if len(dd) else 0.0)


def perf_stats(daily_ret: pd.Series) -> dict:
    daily_ret = daily_ret.dropna()
    if daily_ret.empty:
        return {}
    eq = (1 + daily_ret).cumprod()
    ann_ret = eq.iloc[-1] ** (ANNUAL_TRADING_DAYS / len(daily_ret)) - 1
    ann_vol = daily_ret.std(ddof=0) * np.sqrt(ANNUAL_TRADING_DAYS)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else np.nan
    mdd = max_drawdown(eq)
    calmar = ann_ret / mdd if mdd > 0 else np.nan
    win_rate = (daily_ret > 0).mean()
    return {
        "年化收益率": ann_ret,
        "年化波动率": ann_vol,
        "夏普比率": sharpe,
        "最大回撤": mdd,
        "收益回撤比": calmar,
        "胜率": win_rate,
        "累计收益率": eq.iloc[-1] - 1,
    }


def apply_min_hold(pos: pd.Series, n: int) -> pd.Series:
    if n <= 0:
        return pos.astype(int)
    pos = pos.astype(int).copy()
    out = pos.copy()
    last_change = 0
    cur = int(out.iloc[0])
    for i in range(1, len(out)):
        tgt = int(pos.iloc[i])
        if tgt == cur:
            continue
        if (i - last_change) < n:
            out.iloc[i] = cur
        else:
            cur = tgt
            out.iloc[i] = cur
            last_change = i
    return out.astype(int)


# =========================
# 2) 读数据 & 对齐
# =========================
idx = pd.read_excel(INDEX_FILE)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].astype(str).apply(parse_date)
idx = idx.dropna(subset=[DATE_COL_INDEX]).sort_values(DATE_COL_INDEX)
idx = idx[[DATE_COL_INDEX, OPEN_COL, CLOSE_COL]].rename(
    columns={DATE_COL_INDEX: "date", OPEN_COL: "open", CLOSE_COL: "close"}
)

fac = pd.read_excel(FACTOR_FILE, sheet_name=FACTOR_SHEET)
fac[DATE_COL_FACTOR] = fac[DATE_COL_FACTOR].astype(str).apply(parse_date)
fac = fac.dropna(subset=[DATE_COL_FACTOR]).sort_values(DATE_COL_FACTOR)
fac = fac[[DATE_COL_FACTOR, FACTOR_COL]].rename(
    columns={DATE_COL_FACTOR: "date", FACTOR_COL: "9M_minus_1M"}
)

df = pd.merge(idx, fac, on="date", how="inner")
df = df[(df["date"] >= pd.to_datetime(START_DATE)) & (df["date"] <= pd.to_datetime(END_DATE))].copy()
df = df.sort_values("date").reset_index(drop=True)
print("merged rows:", len(df), "date range:", df["date"].min(), "->", df["date"].max())


# =========================
# 3) 因子处理 & 信号生成（按PPT图二）
# =========================
df["factor_ma"] = df["9M_minus_1M"].rolling(MA_WIN, min_periods=MA_WIN).mean()
df["factor_prev"] = df["factor_ma"].shift(COMPARE_LAG)

# PPT口径：上升 -> 做空；下降 -> 做多（方向=-1）
df["signal_raw"] = np.where(df["factor_ma"] > df["factor_prev"], -1,
                    np.where(df["factor_ma"] < df["factor_prev"],  1, 0))

df["pos_target"] = df["signal_raw"].shift(SIGNAL_LAG).fillna(0)
df["pos"] = apply_min_hold(df["pos_target"], MIN_HOLD_DAYS).astype(int)


# =========================
# 4) 交易成本 & 策略收益（执行价可切换）
# =========================
df["pos_prev"] = df["pos"].shift(1).fillna(0).astype(int)
df["turnover_units"] = (df["pos"] - df["pos_prev"]).abs()
one_side_cost = FEE_RATE + SLIPPAGE
df["cost"] = df["turnover_units"] * one_side_cost

if EXECUTION_PRICE.lower() == "open":
    # 开盘成交：隔夜(close->open) + 日内(open->close)
    df["ret_on"] = df["open"] / df["close"].shift(1) - 1
    df["ret_intra"] = df["close"] / df["open"] - 1
    df["strat_ret"] = df["pos_prev"] * df["ret_on"] + df["pos"] * df["ret_intra"] - df["cost"]

    df["index_ret"] = df["close"].pct_change()
    df["index_nav"] = (1 + df["index_ret"].fillna(0)).cumprod()
    df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()

elif EXECUTION_PRICE.lower() == "close":
    # 收盘成交：close-to-close
    df["ret_cc"] = df["close"].pct_change()
    df["strat_ret"] = df["pos_prev"] * df["ret_cc"] - df["cost"]

    df["index_ret"] = df["ret_cc"]
    df["index_nav"] = (1 + df["index_ret"].fillna(0)).cumprod()
    df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()

else:
    raise ValueError("EXECUTION_PRICE 只能是 'open' 或 'close'。")


# =========================
# 5) 输出绩效
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
# 6) 图一：因子MA vs 指数
# =========================
plt.figure(figsize=(12, 5))
ax1 = plt.gca()
ax1.plot(df["date"], df["factor_ma"], label=f"9m_minus_1m_MA{MA_WIN}（左轴）")
ax1.set_ylabel("Factor MA")

ax2 = ax1.twinx()
ax2.plot(df["date"], df["close"], alpha=0.6, label="中证全指（右轴）")
ax2.set_ylabel("Index Close")

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.title(f"9m_minus_1m_MA{MA_WIN} vs 中证全指走势")
plt.tight_layout()
plt.show()


# =========================
# 7) 图二：策略净值 vs 指数 + 持仓背景
# =========================
plt.figure(figsize=(12, 5))
ax = plt.gca()

long_mask = df["pos_prev"] == 1
short_mask = df["pos_prev"] == -1
ax.fill_between(df["date"], 0, 1, where=long_mask, transform=ax.get_xaxis_transform(),
                alpha=0.12, label="做多区间")
ax.fill_between(df["date"], 0, 1, where=short_mask, transform=ax.get_xaxis_transform(),
                alpha=0.25, label="做空区间")

ax2 = ax.twinx()
ax2.plot(df["date"], df["index_nav"], label="中证全指净值（右轴）", linewidth=1.5)
ax2.plot(df["date"], df["strat_nav"], label="策略净值（右轴）", linewidth=1.8)

ax.set_title(
    f"9m_minus_1m_MA{MA_WIN}-trend（方向=-1, shift_n={COMPARE_LAG}, "
    f"exec={EXECUTION_PRICE}, lag={SIGNAL_LAG}, hold={MIN_HOLD_DAYS}）"
)
ax.set_ylabel("持仓区间（背景）")
ax2.set_ylabel("净值")

lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.tight_layout()
plt.show()


# =========================
# 8) 导出
# =========================
export_cols = [
    "date", "open", "close",
    "9M_minus_1M", "factor_ma", "factor_prev",
    "signal_raw", "pos", "pos_prev", "turnover_units", "cost",
    "strat_ret", "index_ret", "index_nav", "strat_nav",
]
if EXECUTION_PRICE.lower() == "open":
    export_cols += ["ret_on", "ret_intra"]
else:
    export_cols += ["ret_cc"]

out = df[export_cols].copy()
out.to_csv("9m_minus_1m_timing_result.csv", index=False, encoding="utf-8-sig")
print("\n已导出：9m_minus_1m_timing_result.csv")
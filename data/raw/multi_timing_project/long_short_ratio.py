# long_short_ratio_timing.py
# 单因子择时：long_short_ratio（期货前20大会员净多持仓）
# 逻辑：MA5 平滑后，计算滚动120日Z值；Z>1做多，Z<-1做空，否则空仓

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["PingFang SC", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# =========================
# 0) 可调参数区（你只改这里）
# =========================
INDEX_FILE = "中证全指数据2.xlsx"          # 指数数据（date, close...）
FACTOR_FILE = "cffex_if_top20_net.csv"    # 因子数据（date, net_top20...）

DATE_COL_INDEX = "date"
CLOSE_COL = "close"

DATE_COL_FACTOR = "date"       # 你的csv里日期列名（截图是 date）
FACTOR_COL = "net_top20"       # 净多持仓列

START_DATE = "2016-01-01"
END_DATE = "2026-02-10"

MA_WIN = 5                     # 图二：5日平滑
Z_WINDOW = 120                 # 图二：滚动120天
Z_TH = 1.0                     # 图二：阈值=1

SIGNAL_LAG = 1                 # 滞后期/执行延迟：1=次日执行；0=同日执行
MIN_HOLD_DAYS = 3              # 持仓天数n：0不限制；比如 5 表示至少持有5天才允许换向

FEE_RATE = 0.0003              # 单边手续费
SLIPPAGE = 0.0000              # 单边滑点

ANNUAL_TRADING_DAYS = 252


# =========================
# 1) 工具函数
# =========================
def parse_date(s):
    """Robust date parser for yyyymmdd ints/strings, YYYY-MM-DD, and Excel serial dates."""
    if pd.isna(s):
        return pd.NaT

    # already datetime-like
    if isinstance(s, (pd.Timestamp, np.datetime64)):
        return pd.to_datetime(s, errors="coerce")

    # numbers: could be yyyymmdd (e.g. 20160104) OR Excel serial (e.g. 42370)
    if isinstance(s, (int, np.integer)):
        n = int(s)
        # heuristic: 8-digit yyyymmdd
        if 19000101 <= n <= 21001231:
            return pd.to_datetime(str(n), format="%Y%m%d", errors="coerce")
        # heuristic: excel serial date (roughly 1990-01-01 ~ 2025-12-31)
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

    # strings
    s = str(s).strip()
    if s == "":
        return pd.NaT

    # yyyymmdd
    if s.isdigit() and len(s) == 8:
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")

    # common formats
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


def apply_min_hold(pos: pd.Series, min_hold_days: int) -> pd.Series:
    """最小持仓天数限制"""
    if min_hold_days <= 0:
        return pos.astype(int)

    pos = pos.astype(int).copy()
    out = pos.copy()

    last_change = 0
    cur = int(out.iloc[0])

    for i in range(1, len(out)):
        target = int(pos.iloc[i])
        if target == cur:
            continue
        if (i - last_change) < min_hold_days:
            out.iloc[i] = cur
        else:
            cur = target
            out.iloc[i] = cur
            last_change = i

    return out.astype(int)


# =========================
# 2) 读数据 & 对齐
# =========================
idx = pd.read_excel(INDEX_FILE)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].astype(str)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].apply(parse_date)
idx = idx.dropna(subset=[DATE_COL_INDEX]).sort_values(DATE_COL_INDEX)
idx = idx[[DATE_COL_INDEX, CLOSE_COL]].rename(columns={DATE_COL_INDEX: "date", CLOSE_COL: "close"})

fac = pd.read_csv(FACTOR_FILE)
fac[DATE_COL_FACTOR] = fac[DATE_COL_FACTOR].astype(str)
fac[DATE_COL_FACTOR] = fac[DATE_COL_FACTOR].apply(parse_date)
fac = fac.dropna(subset=[DATE_COL_FACTOR]).sort_values(DATE_COL_FACTOR)

# 如果你的csv里包含多个品种（variety列），建议先筛 IF
if "variety" in fac.columns:
    fac = fac[fac["variety"].astype(str).str.upper() == "IF"].copy()

fac = fac[[DATE_COL_FACTOR, FACTOR_COL]].rename(columns={DATE_COL_FACTOR: "date", FACTOR_COL: "net_top20"})

df = pd.merge(idx, fac, on="date", how="inner")
df = df[(df["date"] >= pd.to_datetime(START_DATE)) & (df["date"] <= pd.to_datetime(END_DATE))].copy()
df = df.sort_values("date").reset_index(drop=True)

df["ret"] = df["close"].pct_change()


# =========================
# 3) 因子处理 & 信号生成（按研报图二）
# =========================
df["factor_ma"] = df["net_top20"].rolling(MA_WIN, min_periods=MA_WIN).mean()

# 滚动均值/标准差 -> Z
roll_mean = df["factor_ma"].rolling(Z_WINDOW, min_periods=Z_WINDOW).mean()
roll_std = df["factor_ma"].rolling(Z_WINDOW, min_periods=Z_WINDOW).std(ddof=0)
df["z"] = (df["factor_ma"] - roll_mean) / roll_std

# 阈值触发：Z>1 多；Z<-1 空；否则空仓
df["signal_raw"] = np.where(df["z"] > Z_TH, 1,
                    np.where(df["z"] < -Z_TH, -1, 0))

# 滞后执行
df["pos_target"] = df["signal_raw"].shift(SIGNAL_LAG).fillna(0)

# 最小持仓天数限制（可关）
df["pos"] = apply_min_hold(df["pos_target"], MIN_HOLD_DAYS)


# =========================
# 4) 成本 & 策略收益
# =========================
df["pos_prev"] = df["pos"].shift(1).fillna(0).astype(int)
df["turnover_units"] = (df["pos"] - df["pos_prev"]).abs()

one_side_cost = FEE_RATE + SLIPPAGE
df["cost"] = df["turnover_units"] * one_side_cost

df["strat_ret"] = df["pos_prev"] * df["ret"] - df["cost"]

df["index_nav"] = (1 + df["ret"].fillna(0)).cumprod()
df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()


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
# 6) 图一：因子 vs 指数
# =========================
plt.figure(figsize=(12, 5))
ax1 = plt.gca()
ax1.plot(df["date"], df["factor_ma"], label=f"long_short_ratio_MA{MA_WIN}（左轴）")
ax1.set_ylabel("MA factor")

ax2 = ax1.twinx()
ax2.plot(df["date"], df["close"], alpha=0.6, label="中证全指（右轴）")
ax2.set_ylabel("Index Close")

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.title("long_short_ratio vs 中证全指走势")
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
    f"long_short_ratio_MA{MA_WIN}-Z-score 策略（window={Z_WINDOW}, th={Z_TH}, "
    f"lag={SIGNAL_LAG}, hold={MIN_HOLD_DAYS}）"
)
ax.set_ylabel("持仓区间（背景）")
ax2.set_ylabel("净值")

lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.tight_layout()
plt.show()


# =========================
# 8) 导出结果
# =========================
out = df[["date", "close", "net_top20", "factor_ma", "z",
          "signal_raw", "pos", "ret", "strat_ret", "index_nav", "strat_nav"]].copy()
out.to_csv("long_short_ratio_timing_result.csv", index=False, encoding="utf-8-sig")
print("\n已导出：long_short_ratio_timing_result.csv")
# composite_signal_timing.py
# 总策略：读取 all_signals_v2.csv 的 signal_mean -> 生成(1/-1/0) -> 滞后执行 -> 回测中证全指

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# =================
# 0) 可调参数区
# =================
# --- 文件路径 ---
INDEX_FILE = "中证全指数据2.xlsx"
SIGNAL_FILE = "all_signals_v2.csv"

# --- 列名映射---
DATE_COL_INDEX = "date"
OPEN_COL = "open"
CLOSE_COL = "close"

DATE_COL_SIGNAL = "date"
SIGNAL_MEAN_COL = "signal_mean"   # all_signals_v2.csv 最后一列

# --- 回测区间---
START_DATE = "2021-01-01"
END_DATE   = "2026-01-22"

# --- 执行与持仓参数---
SIGNAL_LAG = 1          # 信号滞后期：1=次日执行
MIN_HOLD_DAYS = 5       # 最小持仓天数n：0=不限制
# --- 信号阈值 ---
EXECUTION_PRICE = "close"  # "open" 或 "close"
SIGNAL_THRESHOLD = 0.13   # signal_mean 绝对值阈值：>阈值才开仓，减少噪声换手
# open: 信号T收盘生成 -> T+1开盘成交 -> 当日收盘计价
# close: 信号T收盘生成 -> T+1收盘成交 -> close-to-close（更简化）

# --- 交易成本---
FEE_RATE = 0.0003       # 单边手续费
SLIPPAGE = 0.0005       # 单边滑点
ANNUAL_TRADING_DAYS = 252

# =================
# 1) 工具函数
# =================
plt.rcParams["font.sans-serif"] = ["PingFang SC", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

def parse_date(x):
    if pd.isna(x):
        return pd.NaT
    s = str(x).strip()
    if s.isdigit() and len(s) == 8:
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(s, errors="coerce")

def apply_min_hold(pos: pd.Series, n: int) -> pd.Series:
    """最小持仓天数：不满足n天不允许换向/清仓"""
    if n <= 0:
        return pos.astype(int)

    pos = pos.astype(int).copy()
    out = pos.copy()

    cur = int(out.iloc[0])
    last_change = 0

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

def max_drawdown(eq: pd.Series) -> float:
    if eq.empty:
        return 0.0
    peak = eq.cummax()
    dd = eq / peak - 1.0
    return float((-dd.min()))

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

# =================
# 2) 读取指数数据
# =================
idx = pd.read_excel(INDEX_FILE)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].astype(str).apply(parse_date)
idx = idx.dropna(subset=[DATE_COL_INDEX]).sort_values(DATE_COL_INDEX)

need_cols = [DATE_COL_INDEX, OPEN_COL, CLOSE_COL]
missing = [c for c in need_cols if c not in idx.columns]
if missing:
    raise ValueError(f"指数文件缺少列：{missing}。请修改参数区 DATE_COL_INDEX/OPEN_COL/CLOSE_COL。")

idx = idx[need_cols].rename(columns={
    DATE_COL_INDEX: "date",
    OPEN_COL: "open",
    CLOSE_COL: "close"
})
idx = idx[(idx["date"] >= pd.to_datetime(START_DATE)) & (idx["date"] <= pd.to_datetime(END_DATE))].copy()
idx = idx.sort_values("date").reset_index(drop=True)

# =================
# 3) 读取综合信号 all_signals_v2.csv
# =================
sig = pd.read_csv(SIGNAL_FILE, encoding="utf-8")
sig[DATE_COL_SIGNAL] = sig[DATE_COL_SIGNAL].astype(str).apply(parse_date)
sig = sig.dropna(subset=[DATE_COL_SIGNAL]).sort_values(DATE_COL_SIGNAL)

if SIGNAL_MEAN_COL not in sig.columns:
    raise ValueError(f"信号文件缺少列：{SIGNAL_MEAN_COL}。请检查 all_signals_v2.csv 列名。")

sig = sig[[DATE_COL_SIGNAL, SIGNAL_MEAN_COL]].rename(columns={
    DATE_COL_SIGNAL: "date",
    SIGNAL_MEAN_COL: "signal_mean"
})

# =================
# 4) 对齐日期轴 + 生成仓位
# =================
df = idx.merge(sig, on="date", how="left").copy()

# 没有信号的日期：默认空仓（你也可以改成 forward-fill，但研报通常不建议偷看）
df["signal_mean"] = df["signal_mean"].fillna(0)

# 规则：>0 多头；<0 空头；=0 空仓
# 加阈值过滤：|signal_mean| 小于阈值视为噪声 -> 空仓
df["signal_raw"] = np.where(df["signal_mean"] > SIGNAL_THRESHOLD, 1,
                     np.where(df["signal_mean"] < -SIGNAL_THRESHOLD, -1, 0))

# 滞后执行（信号 T 收盘产生 -> T+L 执行）
df["pos_target"] = df["signal_raw"].shift(SIGNAL_LAG).fillna(0).astype(int)

# 最小持仓天数
df["pos"] = apply_min_hold(df["pos_target"], MIN_HOLD_DAYS).astype(int)
df["pos_prev"] = df["pos"].shift(1).fillna(0).astype(int)

# =================
# 5) 回测：open / close 两种成交方式
# =================
df["turnover_units"] = (df["pos"] - df["pos_prev"]).abs()
one_side_cost = FEE_RATE + SLIPPAGE
df["cost"] = df["turnover_units"] * one_side_cost

if EXECUTION_PRICE.lower() == "open":
    # 信号T收盘 -> T+1开盘换仓；收益拆成隔夜+日内
    df["ret_on"] = df["open"] / df["close"].shift(1) - 1
    df["ret_intra"] = df["close"] / df["open"] - 1

    # 隔夜收益用“上一日仓位”，日内收益用“当日仓位”
    df["strat_ret"] = df["pos_prev"] * df["ret_on"] + df["pos"] * df["ret_intra"] - df["cost"]

    df["index_ret"] = df["close"].pct_change()

elif EXECUTION_PRICE.lower() == "close":
    # close-to-close：信号T收盘 -> T+1收盘换仓（简化口径）
    df["index_ret"] = df["close"].pct_change()
    df["strat_ret"] = df["pos_prev"] * df["index_ret"] - df["cost"]
else:
    raise ValueError("EXECUTION_PRICE 只能是 'open' 或 'close'")

df["index_nav"] = (1 + df["index_ret"].fillna(0)).cumprod()
df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()

# =================
# 6) 绩效输出
# =================
stats = perf_stats(df["strat_ret"])
print("\n===== 总策略绩效（净） =====")
for k, v in stats.items():
    if isinstance(v, (float, np.floating)) and np.isfinite(v):
        if ("率" in k) or ("回撤" in k):
            print(f"{k}: {v:.2%}")
        else:
            print(f"{k}: {v:.4f}")
    else:
        print(f"{k}: {v}")

# =================
# 7) 图：综合信号 & 净值（仿研报图一）
# =================
plt.figure(figsize=(12, 5))
ax = plt.gca()

# 背景：持仓区间
long_mask = df["pos_prev"] == 1
short_mask = df["pos_prev"] == -1
ax.fill_between(df["date"], 0, 1, where=long_mask, transform=ax.get_xaxis_transform(),
                alpha=0.12, label="做多区间")
ax.fill_between(df["date"], 0, 1, where=short_mask, transform=ax.get_xaxis_transform(),
                alpha=0.25, label="做空区间")

ax2 = ax.twinx()
ax2.plot(df["date"], df["index_nav"], label="中证全指净值（右轴）", linewidth=1.5)
ax2.plot(df["date"], df["strat_nav"], label="总策略净值（右轴）", linewidth=1.8)

ax.set_title(
    f"综合信号总策略（lag={SIGNAL_LAG}, hold={MIN_HOLD_DAYS}, exec={EXECUTION_PRICE}, fee={FEE_RATE}, slip={SLIPPAGE}）"
)
ax.set_ylabel("持仓区间（背景）")
ax2.set_ylabel("净值")

lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.tight_layout()
plt.show()

# =================
# 8) 导出
# =================
out_cols = [
    "date", "open", "close",
    "signal_mean", "signal_raw",
    "pos_target", "pos", "pos_prev",
    "turnover_units", "cost",
    "index_ret", "strat_ret",
    "index_nav", "strat_nav"
]
df[out_cols].to_csv("composite_timing_result.csv", index=False, encoding="utf-8-sig")
print("\n已导出：composite_timing_result.csv")

# =================
# 9) 参数寻优：扫 SIGNAL_THRESHOLD
# =================
def run_backtest_with_threshold(base_df: pd.DataFrame, threshold: float) -> dict:
    d = base_df.copy()

    # 重新生成 signal_raw（带阈值）
    d["signal_raw"] = np.where(d["signal_mean"] > threshold, 1,
                        np.where(d["signal_mean"] < -threshold, -1, 0))

    # 滞后执行
    d["pos_target"] = d["signal_raw"].shift(SIGNAL_LAG).fillna(0).astype(int)

    # 最小持仓
    d["pos"] = apply_min_hold(d["pos_target"], MIN_HOLD_DAYS).astype(int)
    d["pos_prev"] = d["pos"].shift(1).fillna(0).astype(int)

    # 成本
    d["turnover_units"] = (d["pos"] - d["pos_prev"]).abs()
    one_side_cost = FEE_RATE + SLIPPAGE
    d["cost"] = d["turnover_units"] * one_side_cost

    # 回测收益
    if EXECUTION_PRICE.lower() == "open":
        d["ret_on"] = d["open"] / d["close"].shift(1) - 1
        d["ret_intra"] = d["close"] / d["open"] - 1
        d["strat_ret"] = d["pos_prev"] * d["ret_on"] + d["pos"] * d["ret_intra"] - d["cost"]
    else:  # close
        d["index_ret"] = d["close"].pct_change()
        d["strat_ret"] = d["pos_prev"] * d["index_ret"] - d["cost"]

    stats = perf_stats(d["strat_ret"])
    # 补充一些你在选参时很有用的指标
    stats["threshold"] = threshold
    stats["年换手(单位)"] = d["turnover_units"].sum()
    stats["平均持仓绝对值"] = d["pos"].abs().mean()
    return stats


# 你要寻优的阈值网格（可改）
threshold_grid = np.round(np.arange(0.00, 1.0, 0.01), 2)

# base_df：你已经对齐好的 df（包含 date/open/close/signal_mean）
base_df = df[["date", "open", "close", "signal_mean"]].copy()

records = []
for th in threshold_grid:
    s = run_backtest_with_threshold(base_df, float(th))
    records.append(s)

res = pd.DataFrame(records)

# 只保留关键列（你也可以全留）
keep_cols = ["threshold", "年化收益率", "夏普比率", "最大回撤", "收益回撤比", "胜率", "累计收益率", "年换手(单位)", "平均持仓绝对值"]
res = res[keep_cols].sort_values(["夏普比率", "最大回撤"], ascending=[False, True])

res.to_csv("threshold_sweep_results.csv", index=False, encoding="utf-8-sig")
print("已导出：threshold_sweep_results.csv")
print(res.head(10))
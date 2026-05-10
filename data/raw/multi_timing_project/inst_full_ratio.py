import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

# =========================
# 可调参数（集中在这里改）
# =========================
# 回测区间（None 表示不过滤）。支持 'YYYY-MM-DD' 或 'YYYYMMDD'
BACKTEST_START = None   # 例如：'2012-01-01'
BACKTEST_END = None     # 例如：'2025-12-31'

# 交易成本参数（按“单边比例”计）：手续费 + 滑点
FEE_RATE = 0.0001       # 手续费（单边）示例：万3
SLIPPAGE = 0.0001       # 滑点（单边）示例：万5

# 持仓约束：开仓后至少持有 N 天才允许平仓/反手
MIN_HOLD_DAYS = 2       # 0 表示不限制；例如 5 表示至少持有 5 个交易日

# =========================
# 0) 工具函数：列名模糊匹配
# =========================
def pick_col(df: pd.DataFrame, candidates: list[str]) -> str:
    """
    在 df.columns 里，按候选关键词（部分匹配）找最可能的列名。
    找不到就抛错，让你看到有哪些列。
    """
    cols = list(df.columns)
    lower_map = {c: str(c).lower() for c in cols}
    for kw in candidates:
        kw_l = kw.lower()
        for c in cols:
            if kw_l in lower_map[c]:
                return c
    raise ValueError(f"找不到列：{candidates}\n当前列有：{cols}")

def to_datetime_series(s: pd.Series) -> pd.Series:
    """
    把日期列尽可能转成 datetime。
    兼容：
    - 20250102（int/str）
    - 2025-01-02
    - 2025/01/02
    """
    s2 = s.copy()
    # 先转成字符串，避免 int 的问题
    s2 = s2.astype(str).str.strip()
    # 8位数字当作 YYYYMMDD
    mask_8 = s2.str.fullmatch(r"\d{8}")
    out = pd.to_datetime(s2, errors="coerce")
    out.loc[mask_8] = pd.to_datetime(s2.loc[mask_8], format="%Y%m%d", errors="coerce")
    return out

# 可选回测区间解析
def parse_date(d):
    """把 BACKTEST_START/BACKTEST_END 解析成 Timestamp；None 原样返回。"""
    if d is None:
        return None
    s = str(d).strip()
    if len(s) == 8 and s.isdigit():
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(s, errors="coerce")

def max_drawdown(nav: pd.Series) -> float:
    """最大回撤（0~1）"""
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())

def perf_stats(daily_ret: pd.Series, nav: pd.Series, periods_per_year: int = 252) -> dict:
    """
    绩效指标：
    - 年化收益率
    - 年化波动率
    - 夏普（无风险利率=0）
    - 最大回撤
    - 收益回撤比（Calmar = 年化收益 / |最大回撤|）
    - 胜率（策略日收益>0 的比例）
    - 累计收益率
    """
    daily_ret = daily_ret.dropna()
    nav = nav.dropna()

    ann_ret = (nav.iloc[-1] / nav.iloc[0]) ** (periods_per_year / len(nav)) - 1
    ann_vol = daily_ret.std(ddof=0) * np.sqrt(periods_per_year)
    sharpe = np.nan if ann_vol == 0 else ann_ret / ann_vol
    mdd = max_drawdown(nav)
    calmar = np.nan if mdd == 0 else ann_ret / abs(mdd)
    win_rate = (daily_ret > 0).mean()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1

    return {
        "年化收益率": ann_ret,
        "年化波动率": ann_vol,
        "夏普比率": sharpe,
        "最大回撤": abs(mdd),
        "收益回撤比": calmar,
        "胜率": win_rate,
        "累计收益率": total_ret
    }

# =========================
# 1) 读取数据
# =========================
factor_path = "zzqz_2.xlsx"
index_path = "中证全指数据2.xlsx"

# 读取：默认取第一个 sheet，你也可以改成 sheet_name="xxx"
df_f = pd.read_excel(factor_path)
df_i = pd.read_excel(index_path)

# =========================
# 2) 从 zzqz_1.xlsx 构造 inst_full_ratio
#    inst_full_ratio = 主力净流入额 / 总市值
# =========================
# 尝试自动识别日期列（优先包含 date / 日期 / trade）
date_col_f = pick_col(df_f, ["date", "日期", "trade_date", "交易日期"])

main_inflow_amt_col = pick_col(df_f, ["主力净流入额", "主力净流入额[单位]元", "主力净流入金额", "主力净流入"])
mktcap_col_f = pick_col(df_f, ["总市值", "总市值2", "市值"])

df_f = df_f.copy()
df_f["date"] = to_datetime_series(df_f[date_col_f])
df_f = df_f.dropna(subset=["date"]).sort_values("date")

# 转数值（有些 Excel 列可能被读成文本/带逗号）
for c in [main_inflow_amt_col, mktcap_col_f]:
    df_f[c] = pd.to_numeric(df_f[c].astype(str).str.replace(",", ""), errors="coerce")

df_f["inst_full_ratio"] = df_f[main_inflow_amt_col] / df_f[mktcap_col_f]

# 可选：研报有时会“20日平滑”后再看趋势，你想完全复刻可以打开
# df_f["inst_full_ratio"] = df_f["inst_full_ratio"].rolling(20, min_periods=1).mean()

factor = df_f[["date", "inst_full_ratio"]].dropna().set_index("date")

# =========================
# 3) 读取中证全指数据（收盘价）
# =========================
# 日期列可能是 A 列或第一列；优先模糊匹配
date_col_i = pick_col(df_i, ["date", "日期", "trade_date", "交易日期"])
close_col_i = pick_col(df_i, ["close", "收盘", "收盘价"])
open_col_i = pick_col(df_i, ["open", "开盘", "开盘价"])

df_i = df_i.copy()
df_i["date"] = to_datetime_series(df_i[date_col_i])
df_i = df_i.dropna(subset=["date"]).sort_values("date")

df_i[close_col_i] = pd.to_numeric(df_i[close_col_i].astype(str).str.replace(",", ""), errors="coerce")
df_i[open_col_i] = pd.to_numeric(df_i[open_col_i].astype(str).str.replace(",", ""), errors="coerce")

index_px = (
    df_i[["date", open_col_i, close_col_i]]
    .dropna()
    .set_index("date")
    .rename(columns={open_col_i: "open", close_col_i: "close"})
)

# =========================
# 4) 对齐日期（取交集），并计算指数日收益
# =========================
data = index_px.join(factor, how="inner").sort_index()
data["idx_ret"] = data["close"].pct_change()
# 日内收益（开盘 -> 收盘），用于“当日开盘建仓”的那一天
# 注意：open 为 0 或缺失已在上游 dropna 处理

data["idx_ret_oc"] = data["close"] / data["open"] - 1

# =========================
# 4.1) 可选：按回测区间过滤数据
# =========================
_start = parse_date(BACKTEST_START)
_end = parse_date(BACKTEST_END)
if _start is not None:
    data = data.loc[data.index >= _start]
if _end is not None:
    data = data.loc[data.index <= _end]

# 若过滤后没有数据，直接给出可读错误信息
if data.empty:
    raise ValueError(
        "回测区间过滤后 data 为空：请检查 BACKTEST_START/BACKTEST_END 是否写成字符串，"
        "以及两个文件的日期是否有交集。"
    )

# =========================
# 5) 研报交易规则：对比 20 日前因子值
#    上升趋势 => 做多；下降趋势 => 做空
#    注意：信号用昨天生成、今天执行（避免未来函数）
# =========================
shift_n = 20
data["inst_shift"] = data["inst_full_ratio"].shift(shift_n)

# 原始信号：1 或 -1（相等时给 0）
raw_sig = np.sign(data["inst_full_ratio"] - data["inst_shift"])
# 相等时沿用上一仓位，更贴近实盘；开头可能全是 NaN，最后补 0 避免全空
raw_sig = raw_sig.replace(0, np.nan).ffill().fillna(0)
data["signal"] = raw_sig

# =========================
# 5.1) 仓位生成模块：T-1 信号、T 执行 + 最小持有期约束
# =========================
# 原始信号在 t 日收盘后才能确认，因此执行时使用 t-1 的 signal
exec_sig = data["signal"].shift(1)

pos_list = []
hold_days = 0
prev_pos = 0

for s in exec_sig.fillna(0).values:
    desired_pos = int(np.sign(s))  # 目标仓位：-1/0/1

    # 默认保持原仓位
    new_pos = prev_pos

    if desired_pos != prev_pos:
        # 若设定了最小持有期：只限制“平仓/反手”，不限制“从空仓开仓”
        # - prev_pos == 0：允许直接开仓
        # - prev_pos != 0：只有持仓天数达到阈值才允许换仓/平仓/反手
        can_change = (prev_pos == 0) or (MIN_HOLD_DAYS <= 0) or (hold_days >= MIN_HOLD_DAYS)
        if can_change:
            new_pos = desired_pos
            # 换仓后重新计数：当天视为第 1 天持仓
            hold_days = 0

    # 更新持仓天数：只在有仓位时累计
    if new_pos != 0:
        hold_days += 1
    else:
        hold_days = 0

    pos_list.append(new_pos)
    prev_pos = new_pos

data["pos"] = pd.Series(pos_list, index=data.index)

# =========================
# 5.2) 收益计算：持仓收益 - 交易成本
# =========================
# 基础收益（持仓收益）：
# - 若当天从空仓开仓：用 开盘->收盘（因为买入价是开盘价）
# - 否则：用 收盘->收盘（隔夜持有的涨跌用收盘估值更一致）
prev_pos = data["pos"].shift(1).fillna(0)
entry_today = (prev_pos == 0) & (data["pos"] != 0)

strategy_ret_base = pd.Series(0.0, index=data.index)
# 当天开仓：pos * (close/open - 1)
strategy_ret_base.loc[entry_today] = data.loc[entry_today, "pos"] * data.loc[entry_today, "idx_ret_oc"]
# 其余情况：pos * (close/prev_close - 1)
strategy_ret_base.loc[~entry_today] = data.loc[~entry_today, "pos"] * data.loc[~entry_today, "idx_ret"].fillna(0)

data["strategy_ret"] = strategy_ret_base

# 交易成本：当仓位发生变化时计入（单边费率 + 单边滑点）
# 例如：从 -1 反手到 +1，仓位变化=2，成本会按 2 倍计入
cost_rate = FEE_RATE + SLIPPAGE
turnover = data["pos"].diff().abs().fillna(0)
data["strategy_ret"] = data["strategy_ret"] - cost_rate * turnover

# =========================
# 6) 计算净值
# =========================
data["idx_nav"] = (1 + data["idx_ret"].fillna(0)).cumprod()
data["strategy_nav"] = (1 + data["strategy_ret"].fillna(0)).cumprod()

stats = perf_stats(data["strategy_ret"], data["strategy_nav"])
print("策略绩效：")
for k, v in stats.items():
    if "率" in k or k in ["夏普比率", "收益回撤比"]:
        print(f"{k}: {v:.4f}")
    else:
        print(f"{k}: {v:.4f}")

# =========================
# 7) 画图1：因子 vs 中证全指走势（双轴）
# =========================
plt.figure(figsize=(12, 5))
ax1 = plt.gca()
ax2 = ax1.twinx()

ax1.plot(data.index, data["inst_full_ratio"], label="inst_full_ratio（左轴）")
ax2.plot(data.index, data["close"], color="gray", alpha=0.7, label="中证全指（右轴）")

ax1.set_title("inst_full_ratio vs 中证全指走势")
ax1.set_ylabel("inst_full_ratio（左）")
ax2.set_ylabel("指数点位（右）")

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.tight_layout()
plt.show()

# =========================
# 8) 画图2：策略净值 vs 指数净值 + 灰色持仓条
#    灰条：持仓状态（pos=1 或 -1）
# =========================
fig = plt.figure(figsize=(12, 6))
ax = fig.add_subplot(111)

# 灰色持仓条（类似研报竖条背景）
# 这里用 fill_between 做“背景条”，pos=1 时浅灰，pos=-1 时更深（你也可统一灰色）
pos = data["pos"].fillna(0)
ax.fill_between(data.index, 0, 1,
                where=(pos != 0),
                transform=ax.get_xaxis_transform(),
                color="lightgray", alpha=0.6, step="pre")

# 右轴画净值
ax_r = ax.twinx()
ax_r.plot(data.index, data["idx_nav"], label="中证全指净值（右轴）")
ax_r.plot(data.index, data["strategy_nav"], label="策略净值（右轴）", color="red")

ax.set_title(f"inst_full_ratio-trend 策略（方向=1, shift_n={shift_n}）")
ax.set_ylabel("信号（背景）")
ax_r.set_ylabel("净值（右）")

# 图例
lines1, labels1 = ax_r.get_legend_handles_labels()
ax_r.legend(lines1, labels1, loc="upper left")

plt.tight_layout()
plt.show()

# =========================
# 9) 绩效表（打印 + 你也可以画在图里）
# =========================
print("\n【绩效表】")
for k, v in stats.items():
    print(f"{k}: {v:.2%}" if "率" in k and k != "夏普比率" else f"{k}: {v:.2f}")
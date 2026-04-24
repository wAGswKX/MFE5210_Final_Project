# small_full_ratio_v1.py
# 单因子择时：散户资金流（TuShare moneyflow 小单/中单）
# 因子：small_full_ratio = (小单/小单+中单 买卖成交额 或 净流入额) / 指数总市值
# 策略（按PPT）：MA20 平滑后，计算 rolling 20 的Z值；Z>阈值做多，Z<-阈值做空
# 执行：信号T收盘产生 → T+1按(open/close)成交（可调）

import time
import numpy as np
import pandas as pd
import tushare as ts
import matplotlib.pyplot as plt
import tushare.pro.client as client

# tqdm：有就显示进度条，没有也能跑
try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# TuShare老版地址（你之前用的）
client.DataApi._DataApi__http_url = "http://tushare.xyz:5000"

# =================
# 0) 可调参数区
# =================
TUSHARE_TOKEN = "46bc13e3c4f5c5844d0eaf680140c3bc3f1406a9e8d43563e35f199e"   # 你给的token（建议你自己本地替换成真实token）

# 指数/回测标的（中证全指）
INDEX_FILE = "中证全指数据2.xlsx"
DATE_COL_INDEX = "date"
OPEN_COL = "open"
CLOSE_COL = "close"
MARKET_CAP_COL = "总市值"      # 图三那列（不一致就改）

START_DATE = "2021-01-01"
END_DATE   = "2026-01-29"

# ------------------------
# 因子构造：小单口径可调
# ------------------------
# TuShare moneyflow 常见字段（单位以接口为准）：
# buy_sm_amount / sell_sm_amount  = 小单（通常 <5万）
# buy_md_amount / sell_md_amount  = 中单（通常 5-20万）
# buy_lg_amount / sell_lg_amount  = 大单（通常 20-100万）
# buy_elg_amount / sell_elg_amount = 超大单（通常 >100万）
#
# 你要模拟：
#   - 小于5万：sm
#   - 小于20万：sm+md
SMALL_ORDER_MODE = "sm+md"   # 可选："sm" / "md" / "sm+md"
USE_NET_FLOW = False         # True: (买-卖)净流入；False: (买+卖)买卖总金额（更贴近“买卖总金额”）

# 股票池（全A会慢；可先用一个列表验证逻辑）
STOCK_POOL = None   # None=全市场；或 ["000001.SZ","600000.SH",...]

# 取数节流/重试
REQUEST_TIMEOUT = 120
SLEEP_BETWEEN_CALLS = 0.25
RETRY = 3

# ------------------------
# 策略参数（按PPT）
# ------------------------
MA_WIN = 20              # PPT里是MA20
Z_ROLL_WIN = 20          # PPT里滚动20日Z值
Z_TH = 1.0               # 阈值=1（可调）
SIGNAL_LAG = 1           # 1=次日执行
MIN_HOLD_DAYS = 1        # 最小持仓天数n

# 交易成本
FEE_RATE = 0.0003
SLIPPAGE = 0.0000

# 成交价口径（你要求可调）
# "open": 信号T收盘产生 → T+1开盘换仓（更真实）
# "close": 信号T收盘产生 → T+1收盘换仓（简化）
EXECUTION_PRICE = "close"

ANNUAL_TRADING_DAYS = 252

# ==============
# 1) 工具函数
# ==============
plt.rcParams["font.sans-serif"] = ["PingFang SC", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

def parse_date(s):
    if pd.isna(s):
        return pd.NaT
    s = str(s).strip()
    if s.isdigit() and len(s) == 8:
        return pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(s, errors="coerce")

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

def retry_call(func, *args, **kwargs):
    last_e = None
    for _ in range(RETRY):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_e = e
            time.sleep(2)
    raise last_e

# ==============
# 2) 读取指数数据（回测用）
# ==============
idx = pd.read_excel(INDEX_FILE)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].astype(str).apply(parse_date)
idx = idx.dropna(subset=[DATE_COL_INDEX]).sort_values(DATE_COL_INDEX)

need_cols = [DATE_COL_INDEX, OPEN_COL, CLOSE_COL, MARKET_CAP_COL]
missing = [c for c in need_cols if c not in idx.columns]
if missing:
    raise ValueError(f"指数文件缺少列：{missing}。请检查列名并修改参数区映射。")

idx = idx[need_cols].rename(
    columns={DATE_COL_INDEX: "date", OPEN_COL: "open", CLOSE_COL: "close", MARKET_CAP_COL: "total_mv"}
)
idx = idx[(idx["date"] >= pd.to_datetime(START_DATE)) & (idx["date"] <= pd.to_datetime(END_DATE))].copy()
idx = idx.sort_values("date").reset_index(drop=True)

# ==============
# 3) 从TuShare取 moneyflow 并构造 small_full_ratio
# ==============
pro = ts.pro_api(TUSHARE_TOKEN, timeout=REQUEST_TIMEOUT)

def get_trade_dates(start_date, end_date):
    sd = pd.to_datetime(start_date).strftime("%Y%m%d")
    ed = pd.to_datetime(end_date).strftime("%Y%m%d")
    cal = retry_call(pro.trade_cal, exchange="SSE", start_date=sd, end_date=ed, is_open="1", fields="cal_date")
    cal["cal_date"] = cal["cal_date"].astype(str).apply(parse_date)
    cal = cal.dropna().sort_values("cal_date")
    return cal["cal_date"].dt.strftime("%Y%m%d").tolist()

def agg_small_moneyflow(trade_date_yyyymmdd: str) -> float:
    """
    返回当日全市场（或股票池）的“小单/中单金额”聚合值
    """
    fields = "ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount"
    df = retry_call(pro.moneyflow, trade_date=trade_date_yyyymmdd, fields=fields)
    if df is None or df.empty:
        return np.nan

    if STOCK_POOL is not None:
        df = df[df["ts_code"].isin(STOCK_POOL)]
        if df.empty:
            return np.nan

    # 选取口径
    if SMALL_ORDER_MODE == "sm":
        buy = df["buy_sm_amount"].sum()
        sell = df["sell_sm_amount"].sum()
    elif SMALL_ORDER_MODE == "md":
        buy = df["buy_md_amount"].sum()
        sell = df["sell_md_amount"].sum()
    elif SMALL_ORDER_MODE == "sm+md":
        buy = (df["buy_sm_amount"] + df["buy_md_amount"]).sum()
        sell = (df["sell_sm_amount"] + df["sell_md_amount"]).sum()
    else:
        raise ValueError("SMALL_ORDER_MODE 只能是 'sm'/'md'/'sm+md'")

    return (buy - sell) if USE_NET_FLOW else (buy + sell)

def build_small_full_ratio(start_date, end_date):
    tds = get_trade_dates(start_date, end_date)
    print(f"[info] Trading days to fetch: {len(tds)}")

    out = []
    iterator = tds
    if tqdm is not None:
        iterator = tqdm(tds, desc="Fetching moneyflow", unit="day")

    for td in iterator:
        val = agg_small_moneyflow(td)
        out.append((td, val))

        if tqdm is not None:
            try:
                iterator.set_postfix_str(f"trade_date={td}")
            except Exception:
                pass

        time.sleep(SLEEP_BETWEEN_CALLS)

    fac = pd.DataFrame(out, columns=["trade_date", "small_amount"])
    fac["date"] = fac["trade_date"].astype(str).apply(parse_date)
    fac = fac.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    fac = fac[["date", "small_amount"]]

    # 合并指数总市值，构造 ratio
    df = idx.merge(fac, on="date", how="left").copy()
    df["small_full_ratio"] = df["small_amount"] / df["total_mv"]
    return df

df = build_small_full_ratio(START_DATE, END_DATE)

# ==============
# 4) 策略信号：MA20 + rolling Z-score（按PPT）
# ==============
df["factor_raw"] = df["small_full_ratio"]

# MA平滑
df["factor_ma"] = df["factor_raw"].rolling(MA_WIN, min_periods=MA_WIN).mean()

# rolling Z-score（对 factor_ma 做rolling标准化）
roll_mean = df["factor_ma"].rolling(Z_ROLL_WIN, min_periods=Z_ROLL_WIN).mean()
roll_std  = df["factor_ma"].rolling(Z_ROLL_WIN, min_periods=Z_ROLL_WIN).std(ddof=0)
df["z"] = (df["factor_ma"] - roll_mean) / roll_std

# 信号：z>th 做多；z<-th 做空
df["signal_raw"] = np.where(df["z"] > Z_TH, 1,
                    np.where(df["z"] < -Z_TH, -1, 0))

df["pos_target"] = df["signal_raw"].shift(SIGNAL_LAG).fillna(0)
df["pos"] = apply_min_hold(df["pos_target"], MIN_HOLD_DAYS).astype(int)
df["pos_prev"] = df["pos"].shift(1).fillna(0).astype(int)

# ==============
# 5) 回测：open/close 执行
# ==============
df["turnover_units"] = (df["pos"] - df["pos_prev"]).abs()
one_side_cost = FEE_RATE + SLIPPAGE
df["cost"] = df["turnover_units"] * one_side_cost

if EXECUTION_PRICE.lower() == "open":
    # T日信号 → T+1开盘换仓；收益=隔夜(上个收盘到开盘)+日内(开盘到收盘)
    df["ret_on"] = df["open"] / df["close"].shift(1) - 1
    df["ret_intra"] = df["close"] / df["open"] - 1
    df["strat_ret"] = df["pos_prev"] * df["ret_on"] + df["pos"] * df["ret_intra"] - df["cost"]

    df["index_ret"] = df["close"].pct_change()
    df["index_nav"] = (1 + df["index_ret"].fillna(0)).cumprod()
    df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()

elif EXECUTION_PRICE.lower() == "close":
    # close-to-close
    df["ret_cc"] = df["close"].pct_change()
    df["strat_ret"] = df["pos_prev"] * df["ret_cc"] - df["cost"]

    df["index_ret"] = df["ret_cc"]
    df["index_nav"] = (1 + df["index_ret"].fillna(0)).cumprod()
    df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()
else:
    raise ValueError("EXECUTION_PRICE 只能是 'open' 或 'close'")

# ==============
# 6) 绩效输出
# ==============
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

# ==============
# 7) 图一：small_full_ratio（MA20） vs 指数
# ==============
plt.figure(figsize=(12, 5))
ax1 = plt.gca()
ax1.plot(df["date"], df["factor_ma"], label=f"small_full_ratio_MA{MA_WIN}（左轴）")
ax1.set_ylabel("small_full_ratio (MA)")

ax2 = ax1.twinx()
ax2.plot(df["date"], df["close"], alpha=0.6, label="中证全指（右轴）")
ax2.set_ylabel("Index Close")

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.title("small_full_ratio vs 中证全指走势")
plt.tight_layout()
plt.show()

# ==============
# 8) 图二：策略净值 vs 指数 + 持仓背景
# ==============
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
    f"small_full_ratio_MA{MA_WIN}-Zscore(win={Z_ROLL_WIN}, th={Z_TH}, mode={SMALL_ORDER_MODE}, net={USE_NET_FLOW}, "
    f"exec={EXECUTION_PRICE}, lag={SIGNAL_LAG}, hold={MIN_HOLD_DAYS})"
)
ax.set_ylabel("持仓区间（背景）")
ax2.set_ylabel("净值")

lines1, labels1 = ax.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.tight_layout()
plt.show()

# ==============
# 9) 导出
# ==============
out_cols = [
    "date","open","close","total_mv",
    "small_amount","small_full_ratio","factor_ma","z",
    "signal_raw","pos","pos_prev","turnover_units","cost",
    "strat_ret","index_ret","index_nav","strat_nav"
]
df[out_cols].to_csv("small_full_ratio_timing_result.csv", index=False, encoding="utf-8-sig")
print("\n已导出：small_full_ratio_timing_result.csv")
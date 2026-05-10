# if_basis_intercept_timing.py
# 单因子择时：股指期货基差（沪深300 IF）
# 因子：intercept_future = 回归截距项
#   每个交易日，用所有“在交易的IF合约”做横截面回归：
#     basis_rate(contract, t) = a_t + b_t * tau(contract, t) + e
#   其中 tau = 剩余期限（年），a_t 即 intercept_future
#
# 策略（按PPT图二）：滚动20日Z-score；Z>1 做多；Z<-1 做空；否则空仓
# 成交价可切换：信号T收盘产生 → T+1开盘成交 或 T+1收盘成交

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import tushare as ts
import tushare.pro.client as client
client.DataApi._DataApi__http_url = "http://tushare.xyz:5000"
pro = ts.pro_api('46bc13e3c4f5c5844d0eaf680140c3bc3f1406a9e8d43563e35f199e')
# ==============
# 0) 可调参数区
# ==============
TUSHARE_TOKEN = "46bc13e3c4f5c5844d0eaf680140c3bc3f1406a9e8d43563e35f199e"
FUTURE_PREFIX = "IF"             # 沪深300股指期货

INDEX_FILE = "中证全指数据2.xlsx"
DATE_COL_INDEX = "date"
OPEN_COL = "open"
CLOSE_COL = "close"

START_DATE = "2022-01-01"
END_DATE = "2026-01-22"

# 因子构造参数
MIN_CONTRACTS_PER_DAY = 2        # 每天至少多少个合约才做回归（不够就NaN）
TAU_MODE = "calendar"            # "calendar" 用自然日 / "trading" 用交易日（后者需要交易日历）

# 策略参数（图二）
Z_WIN = 20                       # 滚动20日
Z_TH = 1.0                       # 阈值=1
SIGNAL_LAG = 1                   # 1=次日执行（推荐），0=同日（偏理想化）
MIN_HOLD_DAYS = 2                # 最小持仓天数n：0不限制

# 交易成本
FEE_RATE = 0.0003                # 单边手续费
SLIPPAGE = 0.0000                # 单边滑点

# 执行价口径（可切换）
# "open": 信号T收盘生成 → T+1开盘换仓 → 当日收盘计价（更真实）
# "close": 信号T收盘生成 → T+1收盘换仓 → close-to-close（更简化）
EXECUTION_PRICE = "close"


ANNUAL_TRADING_DAYS = 252

# TuShare Pro 初始化（提高超时时间，避免大查询/网络抖动导致 ReadTimeout）
pro = ts.pro_api(TUSHARE_TOKEN, timeout=120)


# =========================
# 1) 通用工具函数
# =========================
plt.rcParams["font.sans-serif"] = ["PingFang SC", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

def parse_date(s):
    if pd.isna(s):
        return pd.NaT
    if isinstance(s, (pd.Timestamp, np.datetime64)):
        return pd.to_datetime(s, errors="coerce")
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

def rolling_zscore(x: pd.Series, win: int) -> pd.Series:
    mu = x.rolling(win, min_periods=win).mean()
    sd = x.rolling(win, min_periods=win).std(ddof=0)
    return (x - mu) / sd


# =========================
# 2) 读取指数数据（用于回测净值）
# =========================
idx = pd.read_excel(INDEX_FILE)
idx[DATE_COL_INDEX] = idx[DATE_COL_INDEX].astype(str).apply(parse_date)
idx = idx.dropna(subset=[DATE_COL_INDEX]).sort_values(DATE_COL_INDEX)
idx = idx[[DATE_COL_INDEX, OPEN_COL, CLOSE_COL]].rename(
    columns={DATE_COL_INDEX: "date", OPEN_COL: "open", CLOSE_COL: "close"}
)
idx = idx[(idx["date"] >= pd.to_datetime(START_DATE)) & (idx["date"] <= pd.to_datetime(END_DATE))].copy()
idx = idx.sort_values("date").reset_index(drop=True)


# =========================
# 3) 从TuShare拉 IF 合约 + 行情，并构造 intercept_future
# =========================
def fetch_intercept_future(start_date: str, end_date: str, token: str) -> pd.DataFrame:
    """
    返回 DataFrame: date, intercept_future, beta, n_contracts
    """
    # 使用全局 pro（已设置更长 timeout）

    sd = pd.to_datetime(start_date).strftime("%Y%m%d")
    ed = pd.to_datetime(end_date).strftime("%Y%m%d")

    # 3.1 取合约基础信息：ts_code, delist_date 等（增加简单重试，防止偶发超时）
    for _ in range(3):
        try:
            fb = pro.fut_basic(exchange="CFFEX", fut_type="1", fields="ts_code,symbol,name,list_date,delist_date")
            break
        except Exception as e:
            last_e = e
            import time
            time.sleep(2)
    else:
        raise last_e
    fb = fb.dropna(subset=["ts_code", "delist_date"])
    fb = fb[fb["ts_code"].str.startswith(FUTURE_PREFIX)].copy()

    # 3.2 取 IF 每日行情（收盘价）: fut_daily
    # 注意：fut_daily一次取全市场会很大；这里用 ts_code 循环更稳但慢一些
    rows = []
    for code, dlist in fb[["ts_code", "delist_date"]].itertuples(index=False):
        # 只拉可能覆盖区间的合约
        if str(dlist) < sd:
            continue
        df = pro.fut_daily(ts_code=code, start_date=sd, end_date=ed, fields="ts_code,trade_date,close")
        if df is None or df.empty:
            continue
        df["delist_date"] = dlist
        rows.append(df)

    if not rows:
        raise RuntimeError("TuShare未拉到任何IF合约行情，请检查token/接口权限/字段名。")

    fut = pd.concat(rows, ignore_index=True)
    fut["trade_date"] = fut["trade_date"].astype(str).apply(parse_date)
    fut["delist_date"] = fut["delist_date"].astype(str).apply(parse_date)
    fut = fut.dropna(subset=["trade_date", "close", "delist_date"]).copy()

    # 3.3 取 HS300 指数收盘（作为现货）用于基差率
    # 用 index_daily：000300.SH
    spot = pro.index_daily(ts_code="000300.SH", start_date=sd, end_date=ed, fields="trade_date,close")
    spot["trade_date"] = spot["trade_date"].astype(str).apply(parse_date)
    spot = spot.rename(columns={"close": "spot_close"})
    spot = spot.dropna().sort_values("trade_date")

    # 合并：得到每个合约每天的 spot_close
    fut = fut.merge(spot, on="trade_date", how="inner")

    # 3.4 计算 tau（剩余期限）
    if TAU_MODE == "calendar":
        fut["tau"] = (fut["delist_date"] - fut["trade_date"]).dt.days / 365.0
    else:
        # trading模式需要交易日历：trade_cal
        cal = pro.trade_cal(exchange="SSE", start_date=sd, end_date=ed, is_open="1", fields="cal_date")
        cal["cal_date"] = cal["cal_date"].astype(str).apply(parse_date)
        cal = cal.dropna().sort_values("cal_date")
        open_days = cal["cal_date"].tolist()
        open_set = set(open_days)
        # 映射交易日序号
        idx_map = {d: i for i, d in enumerate(open_days)}

        def trading_days_to_expiry(td, dl):
            td = pd.to_datetime(td)
            dl = pd.to_datetime(dl)
            if td not in open_set or dl not in open_set:
                return np.nan
            return (idx_map[dl] - idx_map[td]) / ANNUAL_TRADING_DAYS

        fut["tau"] = fut.apply(lambda r: trading_days_to_expiry(r["trade_date"], r["delist_date"]), axis=1)

    fut = fut[(fut["tau"].notna()) & (fut["tau"] > 0)].copy()

    # 3.5 计算基差率：basis_rate = (F - S) / S
    fut["basis_rate"] = (fut["close"] - fut["spot_close"]) / fut["spot_close"]

    # 3.6 按天回归 basis_rate ~ tau，取截距
    out = []
    for d, g in fut.groupby("trade_date"):
        g = g.dropna(subset=["basis_rate", "tau"])
        if len(g) < MIN_CONTRACTS_PER_DAY:
            out.append((d, np.nan, np.nan, len(g)))
            continue

        x = g["tau"].values.astype(float)
        y = g["basis_rate"].values.astype(float)

        # OLS: y = a + b x
        X = np.column_stack([np.ones_like(x), x])
        try:
            coef = np.linalg.lstsq(X, y, rcond=None)[0]
            a, b = float(coef[0]), float(coef[1])
        except Exception:
            a, b = np.nan, np.nan

        out.append((d, a, b, len(g)))

    fac = pd.DataFrame(out, columns=["date", "intercept_future", "beta_tau", "n_contracts"])
    fac = fac.sort_values("date").reset_index(drop=True)
    return fac


fac = fetch_intercept_future(START_DATE, END_DATE, TUSHARE_TOKEN)

# 对齐到指数交易日
df = idx.merge(fac, on="date", how="inner").sort_values("date").reset_index(drop=True)
print("merged rows:", len(df), "date range:", df["date"].min(), "->", df["date"].max())


# =========================
# 4) 策略：rolling 20日 Z-score 阈值
# =========================
df["z"] = rolling_zscore(df["intercept_future"], Z_WIN)

# 图二口径：Z > 1 做多；Z < -1 做空；否则空仓
df["signal_raw"] = np.where(df["z"] > Z_TH, 1,
                    np.where(df["z"] < -Z_TH, -1, 0))

df["pos_target"] = df["signal_raw"].shift(SIGNAL_LAG).fillna(0)
df["pos"] = apply_min_hold(df["pos_target"], MIN_HOLD_DAYS).astype(int)
df["pos_prev"] = df["pos"].shift(1).fillna(0).astype(int)

# =========================
# 5) 回测：可切换执行价 open/close
# =========================
df["turnover_units"] = (df["pos"] - df["pos_prev"]).abs()
one_side_cost = FEE_RATE + SLIPPAGE
df["cost"] = df["turnover_units"] * one_side_cost

if EXECUTION_PRICE.lower() == "open":
    df["ret_on"] = df["open"] / df["close"].shift(1) - 1
    df["ret_intra"] = df["close"] / df["open"] - 1
    df["strat_ret"] = df["pos_prev"] * df["ret_on"] + df["pos"] * df["ret_intra"] - df["cost"]

    df["index_ret"] = df["close"].pct_change()
    df["index_nav"] = (1 + df["index_ret"].fillna(0)).cumprod()
    df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()

elif EXECUTION_PRICE.lower() == "close":
    df["ret_cc"] = df["close"].pct_change()
    df["strat_ret"] = df["pos_prev"] * df["ret_cc"] - df["cost"]

    df["index_ret"] = df["ret_cc"]
    df["index_nav"] = (1 + df["index_ret"].fillna(0)).cumprod()
    df["strat_nav"] = (1 + df["strat_ret"].fillna(0)).cumprod()
else:
    raise ValueError("EXECUTION_PRICE 只能是 'open' 或 'close'。")

# =========================
# 6) 输出绩效
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
# 7) 图一：intercept_future vs 指数
# =========================
plt.figure(figsize=(12, 5))
ax1 = plt.gca()
ax1.plot(df["date"], df["intercept_future"], label="intercept_future（左轴）")
ax1.set_ylabel("intercept_future")

ax2 = ax1.twinx()
ax2.plot(df["date"], df["close"], alpha=0.6, label="中证全指（右轴）")
ax2.set_ylabel("Index Close")

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

plt.title("intercept_future vs 中证全指走势")
plt.tight_layout()
plt.show()


# =========================
# 8) 图二：策略净值 vs 指数 + 持仓背景
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
    f"intercept_future-Zscore（win={Z_WIN}, th={Z_TH}, exec={EXECUTION_PRICE}, "
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
# 9) 导出结果
# =========================
cols = ["date", "open", "close", "intercept_future", "beta_tau", "n_contracts", "z",
        "signal_raw", "pos", "pos_prev", "turnover_units", "cost",
        "strat_ret", "index_ret", "index_nav", "strat_nav"]
if EXECUTION_PRICE.lower() == "open":
    cols += ["ret_on", "ret_intra"]
else:
    cols += ["ret_cc"]

df[cols].to_csv("intercept_future_if_timing_result.csv", index=False, encoding="utf-8-sig")
print("\n已导出：intercept_future_if_timing_result.csv")
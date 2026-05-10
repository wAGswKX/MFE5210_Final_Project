import time
from typing import Optional, List

import pandas as pd
import tushare as ts
from tqdm import tqdm
import tushare.pro.client as client
client.DataApi._DataApi__http_url = "http://tushare.xyz:5000"
ts_pro = ts.pro_api('46bc13e3c4f5c5844d0eaf680140c3bc3f1406a9e8d43563e35f199e')

# =========================
# 可调参数（集中在这里改）
# =========================
TUSHARE_TOKEN = "46bc13e3c4f5c5844d0eaf680140c3bc3f1406a9e8d43563e35f199e"

START_DATE = "20210101"   # 起始日期：YYYYMMDD
END_DATE = "20260122"     # 结束日期：YYYYMMDD

OUTPUT_CSV = "a_share_down_amount_ratio.csv"

# 接口限频与重试（根据你的积分/权限可调整）
SLEEP_SECONDS = 0.35      # 每次请求后睡眠（太快会触发限频）
MAX_RETRY = 5             # 单日数据拉取失败时的重试次数


def get_trade_dates(pro, start: str, end: str) -> List[str]:
    """
    用交易日历获取区间内所有开市日期（YYYYMMDD 列表）。
    TuShare trade_cal 常见日期列名是 cal_date（有些版本也可能叫 trade_date）。
    """
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")

    if "trade_date" in cal.columns:
        date_col = "trade_date"
    elif "cal_date" in cal.columns:
        date_col = "cal_date"
    else:
        raise KeyError(
            f"trade_cal 返回中找不到日期列，现有列：{list(cal.columns)}，前几行：\n{cal.head()}"
        )

    return cal[date_col].tolist()


def fetch_daily_all(pro, trade_date: str) -> pd.DataFrame:
    """
    拉取某个交易日全市场日线数据（A股股票）：
    - ts_code
    - pct_chg
    - amount
    """
    return pro.daily(
        trade_date=trade_date,
        fields="ts_code,trade_date,pct_chg,amount"
    )


def safe_fetch_daily_all(pro, trade_date: str, max_retry: int = 5) -> pd.DataFrame:
    """
    带重试的拉取（应对网络抖动/限频）。
    """
    last_err: Optional[Exception] = None
    for k in range(max_retry):
        try:
            return fetch_daily_all(pro, trade_date)
        except Exception as e:
            last_err = e
            time.sleep(0.8 * (k + 1))  # 指数退避
    raise RuntimeError(f"trade_date={trade_date} 拉取失败，最后错误：{last_err}") from last_err


def main():
    # 1) 初始化 TuShare
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    # 2) 获取交易日列表
    trade_dates = get_trade_dates(pro, START_DATE, END_DATE)
    if not trade_dates:
        raise ValueError("交易日列表为空：请检查 START_DATE/END_DATE 是否写对。")

    results = []

    # 3) 按交易日循环：拉取全市场数据 -> 计算下跌成交额及占比
    for d in tqdm(trade_dates, desc="计算每日下跌成交额占比"):
        df = safe_fetch_daily_all(pro, d, MAX_RETRY)

        if df is None or df.empty:
            results.append({
                "trade_date": d,
                "down_amount": 0.0,
                "total_amount": 0.0,
                "down_ratio": None,
                "n_stocks": 0,
                "n_down": 0,
            })
            time.sleep(SLEEP_SECONDS)
            continue

        # 清洗：确保数值列可计算
        df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        df = df.dropna(subset=["pct_chg", "amount"])

        total_amount = df["amount"].sum()
        down_amount = df.loc[df["pct_chg"] < 0, "amount"].sum()

        down_ratio = (down_amount / total_amount) if total_amount != 0 else None

        results.append({
            "trade_date": d,
            "down_amount": float(down_amount),
            "total_amount": float(total_amount),
            "down_ratio": float(down_ratio) if down_ratio is not None else None,
            "n_stocks": int(len(df)),
            "n_down": int((df["pct_chg"] < 0).sum()),
        })

        time.sleep(SLEEP_SECONDS)

    # 4) 输出 CSV
    out = pd.DataFrame(results).sort_values("trade_date")
    out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n✅ 已生成：{OUTPUT_CSV}")
    print(out.head())


if __name__ == "__main__":
    main()
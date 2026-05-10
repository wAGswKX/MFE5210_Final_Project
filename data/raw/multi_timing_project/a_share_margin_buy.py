import time
from typing import Optional, List, Dict

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

OUTPUT_CSV = "a_share_margin_buy.csv"

# 若你使用私有/镜像服务（例如 tushare.xyz），取消注释并改成你的地址
# import tushare.pro.client as client
# client.DataApi._DataApi__http_url = "http://tushare.xyz:5000"

SLEEP_SECONDS = 0.20
MAX_RETRY = 5


def get_trade_dates(pro, start: str, end: str) -> List[str]:
    """获取区间内开市日期列表（YYYYMMDD）"""
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1")
    if "trade_date" in cal.columns:
        date_col = "trade_date"
    elif "cal_date" in cal.columns:
        date_col = "cal_date"
    else:
        raise KeyError(f"trade_cal 找不到日期列，现有列：{list(cal.columns)}\n{cal.head()}")
    return cal[date_col].tolist()


def fetch_margin_buy(pro, trade_date: str) -> pd.DataFrame:
    """
    拉取某天融资融券汇总数据。
    TuShare 接口：pro.margin()
    关键字段：
      - trade_date
      - exchange_id (SSE/SZSE)
      - rzmre 融资买入额
    """
    # 有的账号/版本支持不传 exchange_id 返回多交易所；但稳妥起见分两次取
    df_sse = pro.margin(trade_date=trade_date, exchange_id="SSE",
                        fields="trade_date,exchange_id,rzmre")
    df_szse = pro.margin(trade_date=trade_date, exchange_id="SZSE",
                         fields="trade_date,exchange_id,rzmre")
    return pd.concat([df_sse, df_szse], ignore_index=True)


def safe_fetch_margin_buy(pro, trade_date: str, max_retry: int = 5) -> pd.DataFrame:
    """带重试的拉取，防止网络抖动/限频"""
    last_err: Optional[Exception] = None
    for k in range(max_retry):
        try:
            return fetch_margin_buy(pro, trade_date)
        except Exception as e:
            last_err = e
            time.sleep(0.8 * (k + 1))
    raise RuntimeError(f"{trade_date} 拉取融资买入额失败：{last_err}") from last_err


def main():
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()

    trade_dates = get_trade_dates(pro, START_DATE, END_DATE)
    if not trade_dates:
        raise ValueError("交易日列表为空：请检查 START_DATE/END_DATE。")

    rows: List[Dict] = []

    for d in tqdm(trade_dates, desc="拉取每日融资买入额 rzmre"):
        df = safe_fetch_margin_buy(pro, d, MAX_RETRY)

        if df is None or df.empty:
            rows.append({
                "trade_date": d,
                "rzmre_sse": None,
                "rzmre_szse": None,
                "rzmre_total": None,
            })
            time.sleep(SLEEP_SECONDS)
            continue

        df["rzmre"] = pd.to_numeric(df["rzmre"], errors="coerce")

        # 分交易所取值（可能一行，也可能多行；用 sum 更稳）
        rzmre_sse = df.loc[df["exchange_id"] == "SSE", "rzmre"].sum(min_count=1)
        rzmre_szse = df.loc[df["exchange_id"] == "SZSE", "rzmre"].sum(min_count=1)

        # 全市场合计
        rzmre_total = pd.Series([rzmre_sse, rzmre_szse]).sum(min_count=1)

        rows.append({
            "trade_date": d,
            "rzmre_sse": float(rzmre_sse) if pd.notna(rzmre_sse) else None,
            "rzmre_szse": float(rzmre_szse) if pd.notna(rzmre_szse) else None,
            "rzmre_total": float(rzmre_total) if pd.notna(rzmre_total) else None,
        })

        time.sleep(SLEEP_SECONDS)

    out = pd.DataFrame(rows).sort_values("trade_date")
    out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n✅ 已生成：{OUTPUT_CSV}")
    print(out.head())


if __name__ == "__main__":
    main()
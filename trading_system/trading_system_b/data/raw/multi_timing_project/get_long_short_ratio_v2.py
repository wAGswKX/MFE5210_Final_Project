import akshare as ak
import pandas as pd
from typing import Dict, Any, List


def fetch_cffex_rank_table(date_yyyymmdd: str, var: str) -> pd.DataFrame:
    """
    获取指定日期和品种的中金所排名表数据，自动识别不同 akshare 版本的参数名。

    Parameters
    ----------
    date_yyyymmdd : str
        日期，格式 YYYYMMDD
    var : str
        品种代码，如 "IF", "IC", "IH", "IM"

    Returns
    -------
    pd.DataFrame
        包含排名表数据的 DataFrame
    """
    # 反射获取函数参数名
    import inspect

    params = inspect.signature(ak.futures_cffex_rank).parameters

    # 方式1：关键字参数（尽量覆盖不同 akshare 版本的参数名）
    kw: Dict[str, Any] = {}

    # date 参数名
    for date_name in ["date", "trade_date", "dt"]:
        if date_name in params:
            kw[date_name] = date_yyyymmdd
            break

    # variety/symbol 参数名
    for var_name in [
        "cffex_var", "var", "variety", "symbol", "code", "contract",
        "future", "futures", "futures_var",
    ]:
        if var_name in params:
            kw[var_name] = var
            break

    # 如果没找到任何“品种”参数名，就不要只传 date（那样会导致不同品种拿到同一份结果）
    if len(kw) == 1 and any(k in kw for k in ["date", "trade_date", "dt"]):
        kw = {}

    # 调用 akshare 函数获取数据
    df = ak.futures_cffex_rank(**kw)
    return df


def calc_top20_net(df: pd.DataFrame) -> Dict[str, int]:
    """
    计算前20名多头、空头持仓量及净持仓量。

    Parameters
    ----------
    df : pd.DataFrame
        排名表数据，包含 'long_position' 和 'short_position' 列

    Returns
    -------
    Dict[str, int]
        包含 'long_top20', 'short_top20', 'net_top20' 三个键的字典
    """
    long_top20 = df["long_position"].iloc[:20].sum()
    short_top20 = df["short_position"].iloc[:20].sum()
    net_top20 = long_top20 - short_top20
    return {
        "long_top20": long_top20,
        "short_top20": short_top20,
        "net_top20": net_top20,
    }


def main():
    dates = [
        "20250102",
        "20250103",
        "20250106",
        "20250107",
        "20250108",
        "20250109",
        "20250110",
        "20250113",
        "20250114",
        "20250115",
        "20250116",
        "20250117",
        "20250120",
        "20250121",
        "20250122",
        "20250123",
        "20250124",
        "20250127",
    ]
    vars_list = ["IC", "IF", "IH", "IM"]

    rows = []

    daily_cache: Dict[str, Dict[str, Dict[str, int]]] = {}

    for d in dates:
        for var in vars_list:
            try:
                data = fetch_cffex_rank_table(d, var)
                res = calc_top20_net(data)

                # 记录当日各品种结果，用于一致性检查
                daily_cache.setdefault(d, {})[var] = res

                # 如果当天 4 个品种都有结果且完全一样，提示可能抓错/品种参数未生效
                if all(v in daily_cache[d] for v in vars_list):
                    vals = [
                        (daily_cache[d][v]["long_top20"], daily_cache[d][v]["short_top20"], daily_cache[d][v]["net_top20"])
                        for v in vars_list
                    ]
                    if len(set(vals)) == 1:
                        print(
                            f"[WARN] {d} IF/IH/IC/IM 结果完全一致（{vals[0]}），"
                            "请检查：1) get_cffex_rank_table 的品种参数名是否匹配；2) dict 里 long/short 表是否识别正确。"
                        )

                rows.append(
                    {
                        "date": d,
                        "variety": var,
                        **res,
                    }
                )
            except Exception as e:
                print(f"Error processing {d} {var}: {e}")

    df_all = pd.DataFrame(rows)
    print(df_all)


if __name__ == "__main__":
    main()

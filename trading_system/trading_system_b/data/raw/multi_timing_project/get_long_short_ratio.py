# -*- coding: utf-8 -*-
"""
只计算 IF（沪深300股指期货）：
每日 前20机构多头持仓合计 - 前20机构空头持仓合计 -> CSV

依赖：
pip install -U akshare pandas
"""

from __future__ import annotations

# =======================
# 可调参数（都在这里）
# =======================
START_DAY = "20160101"          # 起始日期 YYYYMMDD
END_DAY = "20260210"            # 结束日期 YYYYMMDD
VARIETY = "IF"                  # 只用 IF
OUT_CSV = "cffex_if_top20_net.csv"
SLEEP_SEC = 0.2                 # 抓取间隔，避免太快
DEBUG_PRINT_KEYS = False        # True 时会打印 dict 的 keys 方便排查
UPDATE_EXISTING = True        # True: 若 OUT_CSV 已存在，仅补齐缺失的新交易日数据
END_DAY_AUTO_TODAY = True     # True: 忽略 END_DAY，自动更新到今天（本地日期）
# =======================

import inspect
import time
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, List, Tuple

import pandas as pd
import akshare as ak


def daterange_yyyymmdd(start_yyyymmdd: str, end_yyyymmdd: str) -> List[str]:
    start = datetime.strptime(start_yyyymmdd, "%Y%m%d").date()
    end = datetime.strptime(end_yyyymmdd, "%Y%m%d").date()
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


# ============ 增量更新相关辅助 ============
def _today_yyyymmdd() -> str:
    return datetime.today().strftime("%Y%m%d")


def _load_existing_dates(csv_path: str) -> set:
    """读取已存在 CSV 的 date 列，返回已有日期集合（YYYYMMDD）。"""
    if not os.path.exists(csv_path):
        return set()
    try:
        old = pd.read_csv(csv_path, dtype={"date": str})
    except Exception:
        return set()
    if "date" not in old.columns:
        return set()
    return set(
        old["date"].astype(str)
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
        .str.strip()
        .dropna()
        .tolist()
    )


def _next_day_yyyymmdd(day_yyyymmdd: str) -> str:
    d = datetime.strptime(day_yyyymmdd, "%Y%m%d").date()
    return (d + timedelta(days=1)).strftime("%Y%m%d")


def _compute_increment_range(start_day: str, end_day: str, existing_dates: set) -> Optional[Tuple[str, str]]:
    """根据已有日期，计算需要补齐的起止区间。返回 (start,end) 或 None 表示无需更新。"""
    if not existing_dates:
        return (start_day, end_day)

    # 取已有最大日期作为最后更新日
    last = max(existing_dates)
    inc_start = _next_day_yyyymmdd(last)
    if inc_start > end_day:
        return None
    return (inc_start, end_day)


def _to_int_series(s: pd.Series) -> pd.Series:
    return (
        s.astype(str)
        .str.replace(",", "", regex=False)
        .str.strip()
        .replace({"": None, "None": None, "nan": None})
        .astype("float64")
        .fillna(0)
        .astype("int64")
    )


def _find_first_col(df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    return None


def _guess_rank_col(df: pd.DataFrame) -> str:
    c = _find_first_col(df, ["rank", "名次", "排名"])
    if c:
        return c
    # 兜底：找看起来像名次的列
    for col in df.columns:
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().sum() > 0 and s.min() >= 1 and s.max() <= 200:
            return col
    raise RuntimeError(f"无法识别名次列，字段：{list(df.columns)}")


def _guess_oi_col(df: pd.DataFrame, prefer_long: bool) -> str:
    if prefer_long:
        candidates = [
            "long_open_interest", "多头持仓", "持买单量", "持买", "买持仓", "买持", "long_oi", "多单持仓",
        ]
    else:
        candidates = [
            "short_open_interest", "空头持仓", "持卖单量", "持卖", "卖持仓", "卖持", "short_oi", "空单持仓",
        ]

    c = _find_first_col(df, candidates)
    if c:
        return c

    # 兜底：选一个数值列（排除名次列）当持仓量列
    rank_col = _guess_rank_col(df)
    numeric_cols = []
    for col in df.columns:
        if col == rank_col:
            continue
        s = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False), errors="coerce")
        if s.notna().sum() > 0:
            numeric_cols.append(col)
    if not numeric_cols:
        raise RuntimeError(f"无法识别持仓量列，字段：{list(df.columns)}")

    # 选均值最大的数值列（持仓量通常量级最大）
    best = max(
        numeric_cols,
        key=lambda c: float(pd.to_numeric(df[c].astype(str).str.replace(",", "", regex=False), errors="coerce").dropna().mean() or 0.0),
    )
    return best


def fetch_cffex_rank_table(date_yyyymmdd: str, var: str) -> Optional[Any]:
    """
    兼容不同 akshare 版本的 get_cffex_rank_table 参数名差异。
    可能返回 DataFrame 或 dict。
    """
    fn = getattr(ak, "get_cffex_rank_table", None)
    if fn is None:
        raise RuntimeError("未找到 ak.get_cffex_rank_table，请升级 akshare：pip install -U akshare")

    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())

    # 尽量用关键字参数
    kw: Dict[str, Any] = {}

    # date 参数名
    for date_name in ["date", "trade_date", "dt"]:
        if date_name in params:
            kw[date_name] = date_yyyymmdd
            break

    # variety 参数名
    for var_name in ["cffex_var", "var", "variety", "symbol", "code", "contract", "future", "futures", "futures_var"]:
        if var_name in params:
            kw[var_name] = var
            break

    # 如果只传了 date（没找到品种参数名），就改用位置参数，避免“不同品种拿到同一份结果”
    try:
        if kw and not (len(kw) == 1 and any(k in kw for k in ["date", "trade_date", "dt"])):
            data = fn(**kw)
        else:
            data = fn(date_yyyymmdd, var)
    except Exception as e:
        # 不要吞异常，否则会导致 rows 为空却不知道原因
        print(f"[WARN] fetch 失败: date={date_yyyymmdd} var={var} err={repr(e)}")
        return None

    if data is None:
        return None
    # 有些版本会用 False 表示“无数据/非交易日”
    if data is False:
        return None
    # 空 DataFrame 也当 None
    if isinstance(data, pd.DataFrame) and len(data) == 0:
        return None
    # 空 dict 也当 None
    if isinstance(data, dict) and len(data) == 0:
        return None
    return data


def _guess_rank_sum_cols(df: pd.DataFrame) -> Tuple[str, str, str]:
    """从 get_rank_sum_daily 返回中猜测 日期列、多头Top20列、空头Top20列"""
    # 日期列
    date_col = _find_first_col(df, ["date", "日期", "trade_date"]) or df.columns[0]

    # 候选：包含 20 的 long/short
    long_candidates = [c for c in df.columns if ("20" in str(c)) and ("long" in str(c).lower() or "多" in str(c) or "买" in str(c))]
    short_candidates = [c for c in df.columns if ("20" in str(c)) and ("short" in str(c).lower() or "空" in str(c) or "卖" in str(c))]

    # 常见命名兜底
    if not long_candidates:
        long_candidates = [c for c in df.columns if str(c).lower() in {"long_20", "long20", "top20_long", "long_top20"}]
    if not short_candidates:
        short_candidates = [c for c in df.columns if str(c).lower() in {"short_20", "short20", "top20_short", "short_top20"}]

    if not long_candidates or not short_candidates:
        raise RuntimeError(f"无法从 get_rank_sum_daily 结果识别 Top20 多/空列，字段={list(df.columns)}")

    # 若多个候选，优先取列名更短的（更像主列）
    long_col = sorted(long_candidates, key=lambda x: len(str(x)))[0]
    short_col = sorted(short_candidates, key=lambda x: len(str(x)))[0]
    return date_col, long_col, short_col


def fetch_if_net_by_rank_sum_daily(start_day: str, end_day: str, var: str = "IF") -> Optional[pd.DataFrame]:
    """优先使用 get_rank_sum_daily 直接拿 Top20 汇总，稳定且速度快"""
    fn = getattr(ak, "get_rank_sum_daily", None)
    if fn is None:
        return None

    try:
        df = fn(start_day=start_day, end_day=end_day, vars_list=[var])
    except Exception as e:
        print(f"[WARN] get_rank_sum_daily 调用失败: {repr(e)}")
        return None

    if df is None or len(df) == 0:
        return None

    # 有些版本会返回包含多品种的数据，再过滤一次
    var_col = _find_first_col(df, ["var", "variety", "品种", "symbol"])
    if var_col:
        mask = df[var_col].astype(str).str.upper().str.startswith(var.upper())
        df2 = df.loc[mask].copy()
        if len(df2) > 0:
            df = df2

    date_col, long_col, short_col = _guess_rank_sum_cols(df)

    out = pd.DataFrame({
        "date": df[date_col].astype(str),
        "variety": var.upper(),
        "long_top20": pd.to_numeric(df[long_col], errors="coerce").fillna(0).astype("int64"),
        "short_top20": pd.to_numeric(df[short_col], errors="coerce").fillna(0).astype("int64"),
    })
    out["net_top20"] = out["long_top20"] - out["short_top20"]

    # 日期标准化为 YYYYMMDD（如果本来就是就不变）
    out["date"] = out["date"].str.replace("-", "", regex=False).str.replace("/", "", regex=False)

    # get_rank_sum_daily 在部分版本里可能会返回同一天多行（不同合约/不同口径）。
    # 用户需求是“每日 IF 前20多-空”，这里按日期汇总成 1 行/天。
    out = (
        out.groupby("date", as_index=False)
        .agg(
            long_top20=("long_top20", "sum"),
            short_top20=("short_top20", "sum"),
        )
    )
    out.insert(1, "variety", var.upper())
    out["net_top20"] = out["long_top20"] - out["short_top20"]

    return out.sort_values(["date"]).reset_index(drop=True)


# 校验输出 DataFrame 的一致性
def validate_output_df(df: pd.DataFrame) -> pd.DataFrame:
    """对输出结果做一致性校验；不通过就抛异常。返回清洗后的 df。"""
    required_cols = ["date", "variety", "long_top20", "short_top20", "net_top20"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise RuntimeError(f"输出缺少必要列: {missing}，实际列={list(df.columns)}")

    df = df.copy()

    # 规范日期格式：YYYYMMDD
    df["date"] = (
        df["date"].astype(str)
        .str.replace("-", "", regex=False)
        .str.replace("/", "", regex=False)
    )

    # 只允许 IF（防止混入其他品种）
    bad_var = df.loc[df["variety"].astype(str).str.upper() != "IF", "variety"].unique().tolist()
    if bad_var:
        raise RuntimeError(f"发现非 IF 的品种行: {bad_var}")

    # 数值列转 int
    for c in ["long_top20", "short_top20", "net_top20"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        if df[c].isna().any():
            bad_rows = df[df[c].isna()][["date", "variety"]].head(5).to_dict("records")
            raise RuntimeError(f"列 {c} 存在无法转数值的值，示例行={bad_rows}")
        df[c] = df[c].astype("int64")

    # 校验：net == long - short
    calc = df["long_top20"] - df["short_top20"]
    mismatch = df.loc[calc != df["net_top20"], ["date", "long_top20", "short_top20", "net_top20"]]
    if len(mismatch) > 0:
        raise RuntimeError(
            "net_top20 不等于 long_top20-short_top20，示例: "
            + str(mismatch.head(5).to_dict("records"))
        )

    # 校验：同一天只能一行
    dup = df[df.duplicated(subset=["date"], keep=False)].sort_values(["date"])
    if len(dup) > 0:
        raise RuntimeError(
            "存在重复日期（同日多行），示例: "
            + str(dup.head(10).to_dict("records"))
        )

    # 基础合理性：持仓应非负
    if (df["long_top20"] < 0).any() or (df["short_top20"] < 0).any():
        bad = df[(df["long_top20"] < 0) | (df["short_top20"] < 0)][["date", "long_top20", "short_top20"]].head(5)
        raise RuntimeError("发现负的持仓量（不合理），示例: " + str(bad.to_dict("records")))

    return df.sort_values(["date"]).reset_index(drop=True)


def _pick_long_short_from_dict(obj: Dict[str, Any]) -> Optional[Dict[str, pd.DataFrame]]:
    """从 dict 里挑出 long/short 两张表"""
    long_keys = ["long", "多", "买", "持买", "多头"]
    short_keys = ["short", "空", "卖", "持卖", "空头"]

    def find_df(keys: List[str]) -> Optional[pd.DataFrame]:
        for k, v in obj.items():
            if isinstance(v, pd.DataFrame):
                kk = str(k).lower()
                if any(token.lower() in kk for token in keys):
                    return v
        return None

    long_df = find_df(long_keys)
    short_df = find_df(short_keys)

    # 兜底：看列名
    if long_df is None:
        for v in obj.values():
            if isinstance(v, pd.DataFrame) and any("long" in str(c).lower() or "持买" in str(c) or "多" in str(c) for c in v.columns):
                long_df = v
                break
    if short_df is None:
        for v in obj.values():
            if isinstance(v, pd.DataFrame) and any("short" in str(c).lower() or "持卖" in str(c) or "空" in str(c) for c in v.columns):
                short_df = v
                break

    if long_df is None or short_df is None:
        return None
    return {"long": long_df, "short": short_df}


def calc_if_top20_net(data: Any, var: str = "IF") -> Dict[str, int]:
    """
    输入 data（DataFrame 或 dict），输出 IF 的 Top20 多/空合计与净值。
    """
    # 情况1：DataFrame（同表含多空列）
    if isinstance(data, pd.DataFrame):
        df = data
        rank_col = _guess_rank_col(df)
        long_col = _find_first_col(df, ["long_open_interest", "long"]) or _guess_oi_col(df, prefer_long=True)
        short_col = _find_first_col(df, ["short_open_interest", "short"]) or _guess_oi_col(df, prefer_long=False)

        d = df.copy()
        d[rank_col] = pd.to_numeric(d[rank_col], errors="coerce")
        d = d[d[rank_col].between(1, 20, inclusive="both")].copy()

        long_sum = int(_to_int_series(d[long_col]).sum())
        short_sum = int(_to_int_series(d[short_col]).sum())
        return {"long_top20": long_sum, "short_top20": short_sum, "net_top20": long_sum - short_sum}

    # 情况2：dict（常见：多头表/空头表分开）
    if isinstance(data, dict):
        # 先按品种过滤（有些版本会把多个品种塞在一个 dict 里）
        v = var.upper()

        # 2.1 顶层按品种分组：{"IF": {...}}
        if v in {str(k).upper() for k in data.keys()}:
            for k, vv in data.items():
                if str(k).upper() == v and isinstance(vv, dict):
                    data = vv
                    break
        else:
            # 2.2 key 带品种前缀/包含品种： "IF_..." / "...IF..."
            filtered = {}
            for k, vv in data.items():
                kk = str(k).upper()
                if kk.startswith(v + "_") or kk.startswith(v + "-") or kk.startswith(v + " "):
                    filtered[k] = vv
                elif (v in kk) and isinstance(vv, pd.DataFrame):
                    filtered[k] = vv
            if filtered:
                data = filtered

        if DEBUG_PRINT_KEYS:
            print(f"[DEBUG] {var} dict keys sample:", list(data.keys())[:30])

        picked = _pick_long_short_from_dict(data)
        if picked is None:
            raise RuntimeError(f"无法识别多/空表，keys={list(data.keys())}")

        long_df = picked["long"].copy()
        short_df = picked["short"].copy()

        long_rank = _guess_rank_col(long_df)
        short_rank = _guess_rank_col(short_df)
        long_oi = _guess_oi_col(long_df, prefer_long=True)
        short_oi = _guess_oi_col(short_df, prefer_long=False)

        long_df[long_rank] = pd.to_numeric(long_df[long_rank], errors="coerce")
        short_df[short_rank] = pd.to_numeric(short_df[short_rank], errors="coerce")

        long_top = long_df[long_df[long_rank].between(1, 20, inclusive="both")]
        short_top = short_df[short_df[short_rank].between(1, 20, inclusive="both")]

        long_sum = int(_to_int_series(long_top[long_oi]).sum())
        short_sum = int(_to_int_series(short_top[short_oi]).sum())
        return {"long_top20": long_sum, "short_top20": short_sum, "net_top20": long_sum - short_sum}

    raise RuntimeError(f"未知 data 类型：{type(data)}")


def main():
    # 1) 结束日期：可选自动到今天
    end_day = _today_yyyymmdd() if END_DAY_AUTO_TODAY else END_DAY

    # 2) 增量更新：只补齐缺失的新日期
    existing_dates = _load_existing_dates(OUT_CSV) if UPDATE_EXISTING else set()
    inc = _compute_increment_range(START_DAY, end_day, existing_dates)

    if UPDATE_EXISTING and os.path.exists(OUT_CSV) and inc is None:
        print(f"[INFO] {OUT_CSV} 已是最新（最后日期={max(existing_dates)}），无需更新。")
        return

    fetch_start, fetch_end = inc if inc is not None else (START_DAY, end_day)
    print(f"[INFO] 将抓取区间: {fetch_start} -> {fetch_end}（UPDATE_EXISTING={UPDATE_EXISTING}）")

    new_df = None

    # 方案A（优先）：一次性用 get_rank_sum_daily 拉取 Top20 汇总
    df_sum = fetch_if_net_by_rank_sum_daily(fetch_start, fetch_end, VARIETY)
    if df_sum is not None and len(df_sum) > 0:
        new_df = validate_output_df(df_sum)
        print(f"[INFO] get_rank_sum_daily 拉取到 {len(new_df)} 行")
    else:
        print("[INFO] get_rank_sum_daily 未取到数据，开始使用逐日 get_cffex_rank_table 兜底抓取...")

        # 方案B（兜底）：逐日抓取 get_cffex_rank_table
        rows = []
        for d in daterange_yyyymmdd(fetch_start, fetch_end):
            # 若已存在该日期，跳过（防止重复抓取）
            if d in existing_dates:
                continue

            data = fetch_cffex_rank_table(d, VARIETY)
            if data is None:
                # 非交易日/无数据
                time.sleep(SLEEP_SEC)
                continue
            try:
                res = calc_if_top20_net(data, VARIETY)
                rows.append(
                    {
                        "date": d,
                        "variety": VARIETY,
                        "long_top20": res["long_top20"],
                        "short_top20": res["short_top20"],
                        "net_top20": res["net_top20"],
                    }
                )
            except Exception as e:
                print(f"[WARN] {d} {VARIETY} 计算失败：{repr(e)}")
            time.sleep(SLEEP_SEC)

        if rows:
            out_df = pd.DataFrame(rows).sort_values(["date"]).reset_index(drop=True)
            new_df = validate_output_df(out_df)
            print(f"[INFO] 兜底逐日拉取到 {len(new_df)} 行")

    if new_df is None or len(new_df) == 0:
        print("[INFO] 本次没有拉取到任何新数据（可能区间内均为非交易日或接口无数据）。")
        return

    # 3) 合并写回：追加到旧CSV，去重、排序、校验
    if os.path.exists(OUT_CSV):
        old_df = pd.read_csv(OUT_CSV, dtype={"date": str})
        # 兼容旧文件里日期可能带 '-' '/'
        old_df["date"] = (
            old_df["date"].astype(str)
            .str.replace("-", "", regex=False)
            .str.replace("/", "", regex=False)
            .str.strip()
        )
        merged = pd.concat([old_df, new_df], ignore_index=True)
    else:
        merged = new_df.copy()

    merged = (
        merged.drop_duplicates(subset=["date"], keep="last")
        .sort_values(["date"])
        .reset_index(drop=True)
    )

    merged = validate_output_df(merged)
    merged.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print(
        f"已更新：{OUT_CSV} | 新增 {len(new_df)} 行 | 总计 {len(merged)} 行 | 最新日期={merged['date'].iloc[-1]}"
    )


if __name__ == "__main__":
    main()
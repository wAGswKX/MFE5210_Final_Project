import os
import glob
import pandas as pd

# =========================
# 0) 可调参数
# =========================
INPUT_DIR = "./"   # CSV所在目录：比如 "./signals/"
OUTPUT_EXCEL = "all_signals_v2.xlsx"
OUTPUT_CSV = "all_signals_v2.csv"

DATE_COL = "date"
SIGNAL_COL = "signal_raw"

# ✅ 日期筛选（新增）
START_DATE = "2017-01-01"
END_DATE   = "2025-12-31"

# 日期轴模式： "union"=并集（推荐），"intersection"=交集
CALENDAR_MODE = "union"

# 缺失信号填充：None=不填；0=填0（推荐用于后续投票/均值）
FILLNA_VALUE = 0

# 如果你想只合并指定文件，写进列表；否则用None表示自动扫描目录下所有csv
SPECIFIC_FILES = [
    "inst_full_ratio_timing_result.csv",
    "small_full_ratio_timing_result.csv",
    "up_amount_ratio_timing_result.csv",
    "down_amount_ratio_timing_result.csv",
    "margin_buy_timing_result.csv",
    "9m_minus_1m_timing_result.csv",
    "long_short_ratio_timing_result.csv",
    "intercept_future_if_timing_result.csv",
]
# SPECIFIC_FILES = None


# =========================
# 1) 工具函数
# =========================
def parse_date_series(s: pd.Series) -> pd.Series:
    """尽量稳健地把日期解析成 pandas datetime"""
    s = s.astype(str).str.strip()
    mask = s.str.match(r"^\d{8}$")
    out = pd.to_datetime(s.where(~mask, None), errors="coerce")
    out.loc[mask] = pd.to_datetime(s.loc[mask], format="%Y%m%d", errors="coerce")
    out = pd.to_datetime(out, errors="coerce")
    return out

def read_signal_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if DATE_COL not in df.columns or SIGNAL_COL not in df.columns:
        raise ValueError(f"{os.path.basename(path)} 缺少必要列：{DATE_COL} / {SIGNAL_COL}")

    df = df[[DATE_COL, SIGNAL_COL]].copy()
    df[DATE_COL] = parse_date_series(df[DATE_COL])
    df = df.dropna(subset=[DATE_COL]).sort_values(DATE_COL)

    # 同一天可能重复 -> 取最后一条（你也可以改成 mean）
    df = df.drop_duplicates(subset=[DATE_COL], keep="last")
    return df


# =========================
# 2) 收集文件
# =========================
if SPECIFIC_FILES is None:
    csv_paths = sorted(glob.glob(os.path.join(INPUT_DIR, "*.csv")))
else:
    csv_paths = [os.path.join(INPUT_DIR, f) for f in SPECIFIC_FILES]

if not csv_paths:
    raise FileNotFoundError(f"在目录 {INPUT_DIR} 下未找到任何CSV文件")

print(f"将合并 {len(csv_paths)} 个文件：")
for p in csv_paths:
    print(" -", os.path.basename(p))


# =========================
# 3) 读取并建日期轴（并集/交集）
# =========================
dfs = {}
date_sets = []

for path in csv_paths:
    name = os.path.splitext(os.path.basename(path))[0]
    d = read_signal_csv(path).rename(columns={SIGNAL_COL: name})
    dfs[name] = d
    date_sets.append(set(d[DATE_COL]))

if CALENDAR_MODE == "union":
    all_dates = sorted(set().union(*date_sets))
elif CALENDAR_MODE == "intersection":
    all_dates = sorted(set.intersection(*date_sets))
else:
    raise ValueError("CALENDAR_MODE 只能是 'union' 或 'intersection'")

base = pd.DataFrame({DATE_COL: pd.to_datetime(all_dates)})

# ✅ 日期筛选（新增）
sd = pd.to_datetime(START_DATE)
ed = pd.to_datetime(END_DATE)
base = base[(base[DATE_COL] >= sd) & (base[DATE_COL] <= ed)].copy()


# =========================
# 4) 左连接合并成宽表
# =========================
out = base.copy()
for name, d in dfs.items():
    out = out.merge(d, on=DATE_COL, how="left")

out = out.sort_values(DATE_COL)

# 先计算信号均值：缺失按设定处理
signal_cols = [c for c in out.columns if c != DATE_COL]
if FILLNA_VALUE is not None:
    out[signal_cols] = out[signal_cols].fillna(FILLNA_VALUE)

# ✅ 新增：所有信号平均值列（放最后）
out["signal_mean"] = out[signal_cols].mean(axis=1)

# 日期展示更友好
out[DATE_COL] = out[DATE_COL].dt.strftime("%Y-%m-%d")


# =========================
# 5) 输出 Excel + CSV
# =========================
mapping = pd.DataFrame({
    "file": [os.path.basename(p) for p in csv_paths],
    "column_name_in_excel": [os.path.splitext(os.path.basename(p))[0] for p in csv_paths],
})

with pd.ExcelWriter(OUTPUT_EXCEL, engine="openpyxl") as writer:
    out.to_excel(writer, index=False, sheet_name="signals_aligned")
    mapping.to_excel(writer, index=False, sheet_name="mapping")

out.to_csv(OUTPUT_CSV, index=False, encoding="utf-8-sig")

print(f"\n✅ 已生成：{OUTPUT_EXCEL}")
print(f"✅ 已生成：{OUTPUT_CSV}")
print("最后一列 signal_mean = 当天所有子策略 signal 的平均值")
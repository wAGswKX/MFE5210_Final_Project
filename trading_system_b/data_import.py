from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from .config import AppConfig
from .db import Database


TIMING_FILES = [
    "inst_full_ratio_timing_result.csv",
    "small_full_ratio_timing_result.csv",
    "up_amount_ratio_timing_result.csv",
    "down_amount_ratio_timing_result.csv",
    "margin_buy_timing_result.csv",
    "9m_minus_1m_timing_result.csv",
    "long_short_ratio_timing_result.csv",
    "intercept_future_if_timing_result.csv",
]


def _to_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _to_int(value: str | None) -> int | None:
    numeric = _to_float(value)
    if numeric is None:
        return None
    return int(numeric)


def _iter_csv_rows(path: Path) -> Iterable[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield {key.strip(): (value.strip() if isinstance(value, str) else value) for key, value in row.items()}


def import_timing_results(config: AppConfig, db: Database) -> dict[str, int]:
    counts: dict[str, int] = {}
    for filename in TIMING_FILES:
        path = config.raw_data_dir / filename
        if not path.exists():
            db.log("WARNING", "import_timing_results", f"Missing timing result file: {filename}")
            continue

        strategy_name = filename.replace("_timing_result.csv", "")
        signal_rows = []
        market_rows = []
        imported = 0

        for row in _iter_csv_rows(path):
            trade_date = row.get("date")
            if not trade_date:
                continue

            close_value = _to_float(row.get("close"))
            open_value = _to_float(row.get("open"))
            if close_value is not None or open_value is not None:
                market_rows.append(
                    (
                        config.default_symbol,
                        trade_date,
                        open_value,
                        None,
                        None,
                        close_value,
                        None,
                        None,
                        filename,
                    )
                )

            meta = {
                key: value
                for key, value in row.items()
                if key not in {"date", "open", "close", "signal_raw", "pos", "strat_ret"}
            }
            signal_rows.append(
                (
                    strategy_name,
                    config.default_symbol,
                    trade_date,
                    _to_float(row.get("signal_raw")),
                    _to_int(row.get("signal_raw")),
                    _to_float(row.get("pos")),
                    _to_float(row.get("strat_ret")),
                    filename,
                    json.dumps(meta, ensure_ascii=False),
                )
            )
            imported += 1

        db.insert_many(
            """
            INSERT OR REPLACE INTO strategy_signal (
                strategy_name, symbol, ts, signal_value, signal_raw, position_target,
                strategy_return, source_file, meta_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            signal_rows,
        )
        db.insert_many(
            """
            INSERT OR REPLACE INTO market_bar (
                symbol, ts, open, high, low, close, volume, amount, source_file
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            market_rows,
        )
        counts[strategy_name] = imported
        db.log("INFO", "import_timing_results", f"Imported {imported} rows from {filename}")
    return counts

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .config import AppConfig
from .db import Database
from .execution import OrderRequest, SimulatedExecutionEngine


A_STRATEGY_NAME = "quiet_big_buy_plus_followed_big_sell_30s"
A_SYMBOL = "QBPFBS30S_LS"


@dataclass
class AStrategyImportResult:
    nav_rows: int
    signal_rows: int
    latest_ts: str | None
    latest_signal: int | None


NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
    "docrel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}


def _column_letters(cell_ref: str) -> str:
    letters = []
    for char in cell_ref:
        if char.isalpha():
            letters.append(char)
        else:
            break
    return "".join(letters)


def _column_index(cell_ref: str) -> int:
    index = 0
    for char in _column_letters(cell_ref):
        index = index * 26 + (ord(char.upper()) - ord("A") + 1)
    return index - 1


def _excel_serial_to_datetime(serial: float) -> datetime:
    return datetime(1899, 12, 30) + timedelta(days=float(serial))


def _load_shared_strings(zf: ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    shared = []
    for si in root.findall("main:si", NS):
        text_parts = [node.text or "" for node in si.findall(".//main:t", NS)]
        shared.append("".join(text_parts))
    return shared


def _resolve_sheet_path(zf: ZipFile, sheet_name: str) -> str:
    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rel_root.findall("rel:Relationship", NS)
    }
    for sheet in workbook_root.findall("main:sheets/main:sheet", NS):
        if sheet.attrib.get("name") == sheet_name:
            rel_id = sheet.attrib.get("{%s}id" % NS["docrel"])
            target = rel_map[rel_id]
            if target.startswith("/"):
                return target.lstrip("/")
            return f"xl/{target}" if not target.startswith("xl/") else target
    raise ValueError(f"Sheet not found: {sheet_name}")


def _cell_value(cell: ET.Element, shared_strings: list[str]):
    cell_type = cell.attrib.get("t")
    value_node = cell.find("main:v", NS)
    if cell_type == "inlineStr":
        text_node = cell.find("main:is/main:t", NS)
        return text_node.text if text_node is not None else None
    if value_node is None:
        return None
    raw_value = value_node.text
    if raw_value is None:
        return None
    if cell_type == "s":
        return shared_strings[int(raw_value)]
    if cell_type == "b":
        return raw_value == "1"
    try:
        numeric = float(raw_value)
    except ValueError:
        return raw_value
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _load_sheet_rows(workbook_path: Path, sheet_name: str) -> list[tuple]:
    with ZipFile(workbook_path) as zf:
        shared_strings = _load_shared_strings(zf)
        sheet_path = _resolve_sheet_path(zf, sheet_name)
        root = ET.fromstring(zf.read(sheet_path))
        rows = []
        for row_node in root.findall(".//main:sheetData/main:row", NS):
            values: list[object | None] = []
            for cell in row_node.findall("main:c", NS):
                idx = _column_index(cell.attrib.get("r", "A1"))
                while len(values) <= idx:
                    values.append(None)
                values[idx] = _cell_value(cell, shared_strings)
            rows.append(tuple(values))
        return rows


def import_a_strategy_workbook(config: AppConfig, db: Database) -> AStrategyImportResult:
    workbook_path = config.a_strategy_workbook
    if not workbook_path.exists():
        raise FileNotFoundError(f"A-strategy workbook not found: {workbook_path}")

    nav_rows = _load_sheet_rows(workbook_path, "factor_performance")
    if len(nav_rows) < 2:
        raise ValueError("factor_performance sheet is empty.")

    header = nav_rows[0]
    ls_idx = header.index("Long_Short_Group")
    market_rows = []
    signal_rows = []
    prev_nav = None
    latest_ts = None
    latest_signal = None

    for row in nav_rows[1:]:
        trade_dt = row[0]
        nav_value = row[ls_idx]
        if trade_dt is None or nav_value is None:
            continue
        if isinstance(trade_dt, (int, float)):
            trade_dt = _excel_serial_to_datetime(float(trade_dt))
        ts = trade_dt.strftime("%Y-%m-%d")
        close_price = float(nav_value)
        market_rows.append((A_SYMBOL, ts, close_price, None, None, close_price, None, None, workbook_path.name))

        signal_raw = 0
        strategy_return = None
        if prev_nav is not None and prev_nav != 0:
            strategy_return = close_price / prev_nav - 1.0
            if strategy_return > 0:
                signal_raw = 1
            elif strategy_return < 0:
                signal_raw = -1

        signal_rows.append(
            (
                A_STRATEGY_NAME,
                A_SYMBOL,
                ts,
                float(signal_raw),
                signal_raw,
                float(signal_raw),
                strategy_return,
                workbook_path.name,
                None,
            )
        )
        prev_nav = close_price
        latest_ts = ts
        latest_signal = signal_raw

    db.insert_many(
        """
        INSERT OR REPLACE INTO market_bar (
            symbol, ts, open, high, low, close, volume, amount, source_file
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        market_rows,
    )
    db.insert_many(
        """
        INSERT OR REPLACE INTO strategy_signal (
            strategy_name, symbol, ts, signal_value, signal_raw, position_target,
            strategy_return, source_file, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        signal_rows,
    )
    db.log(
        "INFO",
        "a_strategy_bridge",
        "Imported A-strategy workbook into backend",
        {"nav_rows": len(market_rows), "signal_rows": len(signal_rows), "symbol": A_SYMBOL},
    )
    return AStrategyImportResult(
        nav_rows=len(market_rows),
        signal_rows=len(signal_rows),
        latest_ts=latest_ts,
        latest_signal=latest_signal,
    )


def execute_latest_a_signal(config: AppConfig, db: Database, unit_size: float = 100.0) -> dict[str, object]:
    engine = SimulatedExecutionEngine(config, db)
    rows = db.fetch_all(
        """
        SELECT ts, signal_raw
        FROM strategy_signal
        WHERE strategy_name = ? AND symbol = ?
        ORDER BY ts DESC
        LIMIT 1
        """,
        [A_STRATEGY_NAME, A_SYMBOL],
    )
    if not rows:
        raise ValueError("A-strategy signal is not available in the backend.")

    latest = dict(rows[0])
    target_signal = int(latest["signal_raw"] or 0)
    positions = {row["symbol"]: dict(row) for row in db.fetch_all("SELECT * FROM positions")}
    current_qty = float(positions.get(A_SYMBOL, {}).get("qty", 0.0))
    target_qty = target_signal * unit_size
    delta_qty = target_qty - current_qty

    if abs(delta_qty) < 1e-12:
        return {
            "ts": latest["ts"],
            "signal_raw": target_signal,
            "target_qty": target_qty,
            "current_qty": current_qty,
            "action": "hold",
        }

    side = "BUY" if delta_qty > 0 else "SELL"
    order_id = engine.submit_order(
        OrderRequest(
            strategy_name=A_STRATEGY_NAME,
            symbol=A_SYMBOL,
            side=side,
            qty=abs(delta_qty),
            notes="Auto-generated from latest A-strategy signal",
        )
    )
    trade_id = engine.execute_order(order_id)
    return {
        "ts": latest["ts"],
        "signal_raw": target_signal,
        "target_qty": target_qty,
        "current_qty": current_qty,
        "order_id": order_id,
        "trade_id": trade_id,
        "action": "rebalance",
    }

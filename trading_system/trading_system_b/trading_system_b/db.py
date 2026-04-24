from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


class Database:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON;")
            yield conn
            conn.commit()
        finally:
            conn.close()

    def initialize(self, schema_path: Path) -> None:
        with self.connect() as conn:
            conn.executescript(Path(schema_path).read_text(encoding="utf-8"))

    def insert_many(self, sql: str, rows: Iterable[Sequence[object]]) -> None:
        with self.connect() as conn:
            conn.executemany(sql, rows)

    def execute(self, sql: str, params: Sequence[object] | None = None) -> None:
        with self.connect() as conn:
            conn.execute(sql, params or [])

    def fetch_all(self, sql: str, params: Sequence[object] | None = None) -> list[sqlite3.Row]:
        with self.connect() as conn:
            cursor = conn.execute(sql, params or [])
            return cursor.fetchall()

    def log(self, level: str, module: str, message: str, extra: Mapping[str, object] | None = None) -> None:
        extra_json = json.dumps(extra, ensure_ascii=False) if extra is not None else None
        self.execute(
            """
            INSERT INTO system_log (ts, level, module, message, extra_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [utc_now_iso(), level, module, message, extra_json],
        )

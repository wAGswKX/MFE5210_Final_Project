from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    project_root: Path
    initial_cash: float = 1_000_000.0
    fee_rate: float = 0.0003
    slippage_rate: float = 0.0005
    default_symbol: str = "000985.SH"

    @property
    def db_path(self) -> Path:
        return self.project_root / "data" / "processed" / "trading_system_b.sqlite3"

    @property
    def schema_path(self) -> Path:
        return self.project_root / "database" / "schema.sql"

    @property
    def raw_data_dir(self) -> Path:
        return self.project_root / "data" / "raw" / "multi_timing_project"

    @property
    def a_strategy_workbook(self) -> Path:
        return self.project_root / "data" / "raw" / "QuietBigBuyPlusFollowedBigSell30s_backtest_week_neutral.xlsx"

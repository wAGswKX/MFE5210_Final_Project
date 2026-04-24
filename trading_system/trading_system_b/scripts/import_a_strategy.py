from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from trading_system_b.a_strategy_bridge import import_a_strategy_workbook
from trading_system_b.config import AppConfig
from trading_system_b.orchestrator import bootstrap_database


def main() -> None:
    config = AppConfig(project_root=PROJECT_ROOT)
    db = bootstrap_database(config)
    result = import_a_strategy_workbook(config, db)
    print("Imported A strategy workbook:")
    print(f"  nav_rows: {result.nav_rows}")
    print(f"  signal_rows: {result.signal_rows}")
    print(f"  latest_ts: {result.latest_ts}")
    print(f"  latest_signal: {result.latest_signal}")


if __name__ == "__main__":
    main()

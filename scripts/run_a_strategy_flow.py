from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from trading_system_b.a_strategy_bridge import execute_latest_a_signal, import_a_strategy_workbook
from trading_system_b.config import AppConfig
from trading_system_b.orchestrator import bootstrap_database


def main() -> None:
    config = AppConfig(project_root=PROJECT_ROOT)
    db = bootstrap_database(config)
    import_result = import_a_strategy_workbook(config, db)
    execution_result = execute_latest_a_signal(config, db, unit_size=100.0)

    print("A strategy backend flow completed:")
    print(f"  latest_signal_ts: {import_result.latest_ts}")
    print(f"  latest_signal: {import_result.latest_signal}")
    for key, value in execution_result.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    main()

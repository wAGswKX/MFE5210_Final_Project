from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from trading_system_b.config import AppConfig
from trading_system_b.data_import import import_timing_results
from trading_system_b.orchestrator import bootstrap_database


def main() -> None:
    config = AppConfig(project_root=PROJECT_ROOT)
    db = bootstrap_database(config)
    counts = import_timing_results(config, db)
    print("Imported timing result rows:")
    for strategy_name, count in sorted(counts.items()):
        print(f"  {strategy_name}: {count}")


if __name__ == "__main__":
    main()

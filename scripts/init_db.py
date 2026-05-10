from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from trading_system_b.config import AppConfig
from trading_system_b.orchestrator import bootstrap_database


def main() -> None:
    config = AppConfig(project_root=PROJECT_ROOT)
    bootstrap_database(config)
    print(f"Initialized database at: {config.db_path}")


if __name__ == "__main__":
    main()

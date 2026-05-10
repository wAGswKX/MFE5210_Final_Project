from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from trading_system_b.config import AppConfig
from trading_system_b.data_import import import_timing_results
from trading_system_b.execution import SimulatedExecutionEngine
from trading_system_b.orchestrator import bootstrap_database, create_demo_orders


def main() -> None:
    config = AppConfig(project_root=PROJECT_ROOT)
    db = bootstrap_database(config)
    import_timing_results(config, db)
    engine = SimulatedExecutionEngine(config, db)
    order_ids = create_demo_orders(engine, config.default_symbol)

    print("Executed demo orders:")
    for order_id in order_ids:
        print(f"  {order_id}")
    print("Latest account snapshot:")
    print(engine.get_account())
    print("Current positions:")
    for position in engine.get_positions():
        print(f"  {position}")


if __name__ == "__main__":
    main()

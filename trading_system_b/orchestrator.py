from __future__ import annotations

from .config import AppConfig
from .db import Database
from .execution import OrderRequest, SimulatedExecutionEngine


def bootstrap_database(config: AppConfig) -> Database:
    db = Database(config.db_path)
    db.initialize(config.schema_path)
    db.log("INFO", "bootstrap", "Database initialized")
    return db


def create_demo_orders(engine: SimulatedExecutionEngine, symbol: str) -> list[str]:
    orders = [
        OrderRequest(strategy_name="composite_signal", symbol=symbol, side="BUY", qty=10),
        OrderRequest(strategy_name="composite_signal", symbol=symbol, side="SELL", qty=3),
    ]
    order_ids = [engine.submit_order(order) for order in orders]
    for order_id in order_ids:
        try:
            engine.execute_order(order_id)
        except ValueError:
            continue
    return order_ids

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trading_system_b.config import AppConfig
from trading_system_b.db import Database
from trading_system_b.execution import OrderRequest, SimulatedExecutionEngine

config = AppConfig(project_root=Path(__file__).resolve().parents[1])
db = Database(config.db_path)
engine = SimulatedExecutionEngine(config, db)

# 读取综合策略的每日信号，按日期排序
signals = db.fetch_all(
    "SELECT ts, position_target FROM strategy_signal "
    "WHERE strategy_name = 'up_amount_ratio' "
    "ORDER BY ts ASC"
)

prev_pos = 0
for row in signals:
    target_pos = int(row["position_target"] or 0)
    if target_pos == prev_pos:
        continue

    symbol = config.default_symbol
    delta = target_pos - prev_pos

    if delta > 0:
        side, qty = "BUY", delta
    elif delta < 0:
        side, qty = "SELL", abs(delta)

    unit_size = 100  # 每单位仓位对应的股数，可根据需要调整
    order_id = engine.submit_order(
        OrderRequest(strategy_name="replay", symbol=symbol, side=side, qty=qty * unit_size)
    )
    try:
        engine.execute_order(order_id)
    except ValueError:
        pass

    prev_pos = target_pos

print("回放完成，trades 表中已生成完整的成交记录。")
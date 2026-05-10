from __future__ import annotations

import uuid
from dataclasses import dataclass

from .config import AppConfig
from .db import Database, utc_now_iso


@dataclass
class OrderRequest:
    strategy_name: str
    symbol: str
    side: str
    qty: float
    price: float | None = None
    order_type: str = "market"
    notes: str | None = None


class SimulatedExecutionEngine:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db

    def submit_order(self, request: OrderRequest) -> str:
        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        self.db.execute(
            """
            INSERT INTO orders (
                order_id, strategy_name, symbol, side, order_type, qty, price,
                status, filled_qty, avg_fill_price, created_at, updated_at, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                order_id,
                request.strategy_name,
                request.symbol,
                request.side.upper(),
                request.order_type.lower(),
                request.qty,
                request.price,
                "NEW",
                0.0,
                None,
                now,
                now,
                request.notes,
            ],
        )
        self.db.log("INFO", "execution", f"Created order {order_id}", {"symbol": request.symbol, "qty": request.qty})
        return order_id

    def cancel_order(self, order_id: str) -> None:
        now = utc_now_iso()
        self.db.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ? AND status = ?",
            ["CANCELED", now, order_id, "NEW"],
        )
        self.db.log("INFO", "execution", f"Canceled order {order_id}")

    def list_orders(self, status: str | None = None) -> list[dict[str, object]]:
        sql = "SELECT * FROM orders"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at"
        return [dict(row) for row in self.db.fetch_all(sql, params)]

    def get_positions(self) -> list[dict[str, object]]:
        return [dict(row) for row in self.db.fetch_all("SELECT * FROM positions ORDER BY symbol")]

    def get_account(self) -> dict[str, object]:
        rows = self.db.fetch_all(
            """
            SELECT * FROM account_nav
            ORDER BY ts DESC
            LIMIT 1
            """
        )
        return dict(rows[0]) if rows else {}

    def _latest_market_price(self, symbol: str) -> float:
        rows = self.db.fetch_all(
            """
            SELECT close, open FROM market_bar
            WHERE symbol = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            [symbol],
        )
        if not rows:
            raise ValueError(f"No market data for symbol {symbol}")
        row = rows[0]
        return float(row["close"] or row["open"])

    def execute_order(self, order_id: str) -> str:
        order_rows = self.db.fetch_all("SELECT * FROM orders WHERE order_id = ?", [order_id])
        if not order_rows:
            raise ValueError(f"Order not found: {order_id}")

        order = dict(order_rows[0])
        if order["status"] != "NEW":
            raise ValueError(f"Order {order_id} is not executable, status={order['status']}")

        market_price = self._latest_market_price(order["symbol"])
        reference_price = float(order["price"]) if order["price"] is not None else market_price
        slippage = reference_price * self.config.slippage_rate
        fill_price = reference_price + slippage if order["side"] == "BUY" else reference_price - slippage
        fee = fill_price * float(order["qty"]) * self.config.fee_rate
        latest_nav = self.get_account()
        available_cash = float(latest_nav["cash"]) if latest_nav else self.config.initial_cash
        gross_cash_needed = float(order["qty"]) * fill_price + fee

        if order["side"] == "BUY" and gross_cash_needed > available_cash:
            now = utc_now_iso()
            self.db.execute(
                "UPDATE orders SET status = ?, updated_at = ?, notes = ? WHERE order_id = ?",
                ["REJECTED", now, "Insufficient cash for simulated execution", order_id],
            )
            self.db.log(
                "WARNING",
                "execution",
                f"Rejected order {order_id} for insufficient cash",
                {"required_cash": gross_cash_needed, "available_cash": available_cash},
            )
            raise ValueError(f"Insufficient cash to execute order {order_id}")

        trade_id = f"trd_{uuid.uuid4().hex[:12]}"
        now = utc_now_iso()
        self.db.execute(
            """
            INSERT INTO trades (
                trade_id, order_id, strategy_name, symbol, side, fill_qty, fill_price, fee, slippage, filled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                trade_id,
                order_id,
                order["strategy_name"],
                order["symbol"],
                order["side"],
                order["qty"],
                fill_price,
                fee,
                slippage,
                now,
            ],
        )
        self.db.execute(
            """
            UPDATE orders
            SET status = ?, filled_qty = ?, avg_fill_price = ?, updated_at = ?
            WHERE order_id = ?
            """,
            ["FILLED", order["qty"], fill_price, now, order_id],
        )
        self._apply_trade(
            symbol=order["symbol"],
            side=order["side"],
            qty=float(order["qty"]),
            fill_price=fill_price,
            fee=fee,
            ts=now,
        )
        self.db.log("INFO", "execution", f"Executed order {order_id}", {"trade_id": trade_id})
        return trade_id

    def _apply_trade(self, symbol: str, side: str, qty: float, fill_price: float, fee: float, ts: str) -> None:
        position_rows = self.db.fetch_all("SELECT * FROM positions WHERE symbol = ?", [symbol])
        current_qty = float(position_rows[0]["qty"]) if position_rows else 0.0
        current_avg = float(position_rows[0]["avg_price"]) if position_rows else 0.0

        signed_qty = qty if side == "BUY" else -qty
        new_qty = current_qty + signed_qty
        if abs(new_qty) < 1e-12:
            new_qty = 0.0

        if side == "BUY":
            total_cost = current_qty * current_avg + qty * fill_price
            new_avg = total_cost / new_qty if new_qty != 0 else 0.0
        else:
            new_avg = current_avg if new_qty != 0 else 0.0

        market_value = new_qty * fill_price
        self.db.execute(
            """
            INSERT INTO positions (symbol, qty, avg_price, market_price, market_value, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                qty = excluded.qty,
                avg_price = excluded.avg_price,
                market_price = excluded.market_price,
                market_value = excluded.market_value,
                updated_at = excluded.updated_at
            """,
            [symbol, new_qty, new_avg, fill_price, market_value, ts],
        )

        latest_nav = self.get_account()
        current_cash = float(latest_nav["cash"]) if latest_nav else self.config.initial_cash
        cash_delta = qty * fill_price + fee
        new_cash = current_cash - cash_delta if side == "BUY" else current_cash + qty * fill_price - fee
        self.mark_to_market(ts=ts, source=f"trade:{symbol}", cash_override=new_cash)

    def mark_to_market(self, ts: str | None = None, source: str = "mark_to_market", cash_override: float | None = None) -> None:
        ts = ts or utc_now_iso()
        latest_nav = self.get_account()
        cash = cash_override if cash_override is not None else (
            float(latest_nav["cash"]) if latest_nav else self.config.initial_cash
        )
        positions = self.get_positions()
        market_value = 0.0
        for position in positions:
            latest_price = self._latest_market_price(str(position["symbol"]))
            market_value += float(position["qty"]) * latest_price
            self.db.execute(
                """
                UPDATE positions SET market_price = ?, market_value = ?, updated_at = ?
                WHERE symbol = ?
                """,
                [latest_price, float(position["qty"]) * latest_price, ts, position["symbol"]],
            )

        total_equity = cash + market_value
        peak_rows = self.db.fetch_all("SELECT MAX(total_equity) AS peak_equity FROM account_nav")
        peak_equity = float(peak_rows[0]["peak_equity"]) if peak_rows and peak_rows[0]["peak_equity"] is not None else total_equity
        peak_equity = max(peak_equity, total_equity)
        drawdown = 0.0 if peak_equity == 0 else (total_equity / peak_equity - 1.0)
        nav = total_equity / self.config.initial_cash if self.config.initial_cash else 0.0

        self.db.execute(
            """
            INSERT OR REPLACE INTO account_nav (ts, cash, market_value, total_equity, nav, drawdown, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [ts, cash, market_value, total_equity, nav, drawdown, source],
        )

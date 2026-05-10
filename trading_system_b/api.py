from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import AppConfig
from .db import Database
from .execution import OrderRequest, SimulatedExecutionEngine


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _json_default(value):
    return str(value)


def _rows_to_dicts(rows) -> list[dict[str, object]]:
    return [dict(row) for row in rows]


class TradingApi:
    def __init__(self, config: AppConfig):
        self.config = config
        self.db = Database(config.db_path)
        self.engine = SimulatedExecutionEngine(config, self.db)

    def health(self) -> dict[str, object]:
        return {"status": "ok", "database": str(self.config.db_path)}

    def orders(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        status = query.get("status", [None])[0]
        sql = "SELECT * FROM orders"
        params: list[object] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        return _rows_to_dicts(self.db.fetch_all(sql, params))

    def trades(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        symbol = query.get("symbol", [None])[0]
        sql = "SELECT * FROM trades"
        params: list[object] = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol)
        sql += " ORDER BY filled_at DESC"
        return _rows_to_dicts(self.db.fetch_all(sql, params))

    def positions(self) -> list[dict[str, object]]:
        return self.engine.get_positions()

    def account(self) -> dict[str, object]:
        return self.engine.get_account()

    def nav(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        limit = int(query.get("limit", ["500"])[0])
        limit = max(1, min(limit, 5000))
        return _rows_to_dicts(
            self.db.fetch_all(
                """
                SELECT * FROM account_nav
                ORDER BY ts DESC
                LIMIT ?
                """,
                [limit],
            )
        )

    def signals(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        strategy_name = query.get("strategy_name", [None])[0]
        symbol = query.get("symbol", [None])[0]
        limit = int(query.get("limit", ["500"])[0])
        limit = max(1, min(limit, 5000))

        sql = "SELECT * FROM strategy_signal"
        clauses = []
        params: list[object] = []
        if strategy_name:
            clauses.append("strategy_name = ?")
            params.append(strategy_name)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return _rows_to_dicts(self.db.fetch_all(sql, params))

    def market_bars(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        symbol = query.get("symbol", [None])[0]
        limit = int(query.get("limit", ["500"])[0])
        limit = max(1, min(limit, 5000))

        sql = "SELECT * FROM market_bar"
        params: list[object] = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol)
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return _rows_to_dicts(self.db.fetch_all(sql, params))

    def logs(self, query: dict[str, list[str]]) -> list[dict[str, object]]:
        level = query.get("level", [None])[0]
        limit = int(query.get("limit", ["500"])[0])
        limit = max(1, min(limit, 5000))

        sql = "SELECT * FROM system_log"
        params: list[object] = []
        if level:
            sql += " WHERE level = ?"
            params.append(level)
        sql += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        return _rows_to_dicts(self.db.fetch_all(sql, params))

    def create_order(self, body: dict[str, object]) -> dict[str, object]:
        request = OrderRequest(
            strategy_name=str(body.get("strategy_name", "manual_api")),
            symbol=str(body["symbol"]),
            side=str(body["side"]),
            qty=float(body["qty"]),
            price=float(body["price"]) if body.get("price") is not None else None,
            order_type=str(body.get("order_type", "market")),
            notes=str(body.get("notes")) if body.get("notes") is not None else "Created from API",
        )
        order_id = self.engine.submit_order(request)
        auto_execute = bool(body.get("auto_execute", True))
        result: dict[str, object] = {"order_id": order_id, "status": "NEW"}
        if auto_execute:
            trade_id = self.engine.execute_order(order_id)
            result.update({"trade_id": trade_id, "status": "FILLED"})
        return result

    def cancel_order(self, order_id: str) -> dict[str, object]:
        self.engine.cancel_order(order_id)
        return {"order_id": order_id, "status": "CANCELED"}


class TradingApiHandler(BaseHTTPRequestHandler):
    api: TradingApi

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_OPTIONS(self) -> None:
        self._send_json({"status": "ok"})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path == "/health":
                payload = self.api.health()
            elif path == "/orders":
                payload = self.api.orders(query)
            elif path == "/trades":
                payload = self.api.trades(query)
            elif path == "/positions":
                payload = self.api.positions()
            elif path == "/account":
                payload = self.api.account()
            elif path == "/nav":
                payload = self.api.nav(query)
            elif path == "/signals":
                payload = self.api.signals(query)
            elif path == "/market-bars":
                payload = self.api.market_bars(query)
            elif path == "/logs":
                payload = self.api.logs(query)
            else:
                self._send_json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(payload)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        try:
            if path == "/orders":
                payload = self.api.create_order(self._read_json_body())
                self._send_json(payload, HTTPStatus.CREATED)
                return

            if path.startswith("/orders/") and path.endswith("/cancel"):
                order_id = path.split("/")[2]
                payload = self.api.cancel_order(order_id)
                self._send_json(payload)
                return

            self._send_json({"error": f"Unknown endpoint: {path}"}, HTTPStatus.NOT_FOUND)
        except KeyError as exc:
            self._send_json({"error": f"Missing required field: {exc}"}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args) -> None:
        return


def create_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    config = AppConfig(project_root=PROJECT_ROOT)
    TradingApiHandler.api = TradingApi(config)
    return ThreadingHTTPServer((host, port), TradingApiHandler)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = create_server(host, port)
    print(f"Trading API running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()

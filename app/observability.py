"""Logging estruturado (JSON) com request-id por requisição (Seção 6.3)."""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status", "duration_ms"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


class RequestContextMiddleware:
    """Atribui request-id, mede duração e loga ponta a ponta.

    Middleware ASGI puro (Seção 2.3/M5 do plano de melhorias) — sem
    ``BaseHTTPMiddleware``. ``request_id`` vai em ``scope["state"]`` (mesmo
    dict que ``Request.state`` lê), visível para as rotas e para o handler de
    exceção não tratada em ``app/main.py``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._log = logging.getLogger("request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        method = scope["method"]
        path = scope["path"]
        status_code: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        start = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration = round((time.perf_counter() - start) * 1000, 2)
            self._log.exception(
                "request_error",
                extra={
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "duration_ms": duration,
                },
            )
            raise
        duration = round((time.perf_counter() - start) * 1000, 2)
        self._log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": method,
                "path": path,
                "status": status_code,
                "duration_ms": duration,
            },
        )

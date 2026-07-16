"""Logging estruturado (JSON) com request-id por requisição (Seção 6.3) e
observabilidade mínima (Sentry + métricas locais — Sprint 2 / item 2.6)."""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid

import sentry_sdk
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__, metrics
from app.config import Settings

REQUEST_ID_HEADER = "X-Request-ID"

# Rotas de polling HTMX que suportam resposta condicional 304 (ETag) — Seção
# 2.2 do plano mestre. Usado só para calcular a taxa de acerto do 304 no
# ``/metrics``; os demais fragmentos de chat fazem polling sem ETag (Realtime
# cobre a atualização em tempo real, o polling é só o fallback).
_ROTAS_POLLING_304 = frozenset({"/workspace/fila/fragmento"})


def configure_sentry(settings: Settings) -> None:
    """Liga o Sentry se ``SENTRY_DSN`` estiver configurada; senão é um no-op
    (``sentry_sdk.init()`` nunca roda e ``capture_exception()`` some sem
    efeito — mesmo padrão de integração opcional do Mailgun/WhatsApp)."""
    if not settings.sentry_dsn:
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=f"portal-chamados-bondmann@{__version__}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )


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
        is_polling = path in _ROTAS_POLLING_304
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
            # Exceção não tratada que escapou até aqui (fora do handler central
            # de app/main.py, ex.: erro em outro middleware) — status_code pode
            # não ter sido escrito ainda; 599 sinaliza "sem resposta enviada".
            metrics.registrar_request(path, status_code or 599, duration, is_polling=is_polling)
            raise
        duration = round((time.perf_counter() - start) * 1000, 2)
        metrics.registrar_request(path, status_code or 0, duration, is_polling=is_polling)
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

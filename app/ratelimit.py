"""Rate limiting compartilhado (slowapi) — Seção 2.4 do plano mestre.

Um único ``Limiter`` para toda a app, importável tanto pela fábrica (`main.py`)
quanto pelas rotas (ex.: abertura de chamado no portal), sem import circular.
IP real atrás de proxy (Railway/Vercel) via ``X-Forwarded-For``.
Storage in-memory por processo (MVP); migrar para Redis se houver >1 réplica.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip, default_limits=[])

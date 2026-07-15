"""Rate limiting compartilhado (slowapi) — Seção 2.4 do plano mestre.

Um único ``Limiter`` para toda a app, importável tanto pela fábrica (`main.py`)
quanto pelas rotas (ex.: abertura de chamado no portal), sem import circular.
IP real atrás de proxy (Railway) via ``X-Forwarded-For``.
Storage in-memory por processo (MVP); migrar para Redis se houver >1 réplica.
"""

from __future__ import annotations

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def client_ip(request: Request) -> str:
    """IP do cliente para fins de rate limit (Sprint 1 / item 1.1, A5).

    O container só é alcançável através do proxy do Railway (um único hop
    confiável); qualquer entrada anterior no ``X-Forwarded-For`` foi escrita
    pelo próprio cliente e é forjável. Cada proxy **acrescenta** ao final da
    lista o IP de quem lhe fez a requisição — logo o IP confiável é o
    **último** da cadeia (o que o Railway observou), nunca o primeiro. Usar
    o primeiro (como antes) permite contornar o rate limit forjando o header.
    """
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        partes = [p.strip() for p in fwd.split(",") if p.strip()]
        if partes:
            return partes[-1]
    return get_remote_address(request)


limiter = Limiter(key_func=client_ip, default_limits=[])

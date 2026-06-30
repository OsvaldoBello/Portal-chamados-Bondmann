"""Configuração do Jinja2 (SSR) — autoescape sempre ligado (Seção 3.10)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.security.csrf import CSRF_HEADER, get_csrf

_TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Jinja2Templates já liga autoescape para .html; reforçado explicitamente.
templates.env.autoescape = True


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    """Renderiza um template injetando contexto comum (CSRF, request).

    Emite um token CSRF, embute seu valor no contexto (para ``hx-headers`` e
    campos hidden) e seta o cookie assinado correspondente na MESMA resposta,
    mantendo o par cookie/header coerente para o double-submit (Seção 3.5).
    """
    csrf = get_csrf()
    token = csrf.issue()
    ctx = {"csrf_header": CSRF_HEADER, "csrf_token": token}
    if context:
        ctx.update(context)
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    csrf.set_cookie(response, token)
    return response

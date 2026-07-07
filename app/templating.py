"""Configuração do Jinja2 (SSR) — autoescape sempre ligado (Seção 3.10)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.domain.sla_visual import barra_sla, estado_sla
from app.security.csrf import CSRF_HEADER, get_csrf

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_TZ = ZoneInfo("America/Sao_Paulo")  # exibição em horário de Brasília (Seção 5.2)

# Metadados de UI por status/prioridade (tokens da marca; Seção 5.1).
STATUS_META = {
    "NOVO":           {"label": "Novo",           "dot": "bg-st_novo",   "text": "text-st_novo",
                       "bg": "bg-st_novo/10",   "icon": "M12 4.5v15m7.5-7.5h-15"},
    "A_FAZER":        {"label": "A fazer",        "dot": "bg-amber-500", "text": "text-amber-500",
                       "bg": "bg-amber-500/10", "icon": "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"},
    "EM_ATENDIMENTO": {"label": "Em atendimento", "dot": "bg-st_atend",  "text": "text-st_atend",
                       "bg": "bg-st_atend/10",  "icon": "M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z"},
    "AGUARDANDO":     {"label": "Aguardando",     "dot": "bg-st_aguard", "text": "text-st_aguard",
                       "bg": "bg-st_aguard/10", "icon": "M15.75 5.25v13.5m-7.5-13.5v13.5"},
    "RESOLVIDO":      {"label": "Resolvido",      "dot": "bg-st_resolv", "text": "text-st_resolv",
                       "bg": "bg-st_resolv/10", "icon": "M4.5 12.75l6 6 9-13.5"},
}
PRIORIDADE_META = {
    "BAIXA":   {"label": "Baixa",   "text": "text-pr_baixa"},
    "MEDIA":   {"label": "Média",   "text": "text-pr_media"},
    "ALTA":    {"label": "Alta",    "text": "text-pr_alta"},
    "URGENTE": {"label": "Urgente", "text": "text-pr_urgente"},
}


def fmt_dt(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Formata um ``timestamptz`` (UTC no banco) em horário de Brasília."""
    if value is None:
        return "—"
    return value.astimezone(_TZ).strftime(fmt)


templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Jinja2Templates já liga autoescape para .html; reforçado explicitamente.
templates.env.autoescape = True
templates.env.globals.update(
    STATUS_META=STATUS_META,
    PRIORIDADE_META=PRIORIDADE_META,
    fmt_dt=fmt_dt,
    estado_sla=estado_sla,   # indicador visual de SLA (Fase 4)
    barra_sla=barra_sla,     # barra de progresso de SLA (Fase 3 do usuário)
)


def render(request: Request, name: str, context: dict | None = None, status_code: int = 200):
    """Renderiza um template injetando contexto comum (CSRF, request).

    Emite um token CSRF, embute seu valor no contexto (para ``hx-headers`` e
    campos hidden) e seta o cookie assinado correspondente na MESMA resposta,
    mantendo o par cookie/header coerente para o double-submit (Seção 3.5).
    """
    csrf = get_csrf()
    token = csrf.get_or_issue(request)
    ctx = {"csrf_header": CSRF_HEADER, "csrf_token": token}
    if context:
        ctx.update(context)
    response = templates.TemplateResponse(request, name, ctx, status_code=status_code)
    csrf.set_cookie(response, token)
    return response

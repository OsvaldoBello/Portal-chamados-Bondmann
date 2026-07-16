"""Harness de PREVIEW visual (somente dev) — NÃO faz parte do app de produção.

Renderiza cada tela dos templates Jinja reais com dados de exemplo e serve o
CSS compilado de verdade, para você inspecionar TODO o visual localmente sem
precisar de Supabase/Docker. A navegação da sidebar funciona entre as telas.

Rodar:
    .venv\\Scripts\\uvicorn preview_screens:app --port 8080
Abrir:  http://localhost:8080/  (cai no login; "Entrar" leva ao portal)

Arquivo descartável — pode apagar quando terminar o acabamento visual.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app.templating import templates

_ENV = templates.env
_STATIC = Path(__file__).parent / "app" / "static"
_NOW = datetime.now(UTC)

STATUS_VALIDOS = ["NOVO", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO"]
PRIORIDADES = ["BAIXA", "MEDIA", "ALTA", "URGENTE"]

_COMMON = {
    "csrf_header": "X-CSRF-Token",
    "csrf_token": "preview-token",
    "perfil": {"nome": "Marina Rocha"},
    # Sem Realtime no preview (não injeta os scripts do chat).
    "supabase_url": None, "anon_key": None, "access_token": None,
}


def _r(name: str, **ctx) -> HTMLResponse:
    return HTMLResponse(_ENV.get_template(name).render(**{**_COMMON, **ctx}))


def _chamado(cid, codigo, titulo, status, prioridade, dept, *, cat=None,
             cliente="Marina Rocha", operador=None, prazo_min=600,
             resolvido=False, nota=None, coment=None):
    return {
        "id": cid, "codigo": codigo, "titulo": titulo, "status": status,
        "prioridade": prioridade, "departamento": dept, "categoria": cat,
        "cliente_nome": cliente, "operador_nome": operador, "operador_id": None,
        "descricao": ("Ao iniciar o processo, o equipamento apresentou falha "
                      "intermitente.\nSegue o lote e a linha de produção afetada."),
        "created_at": _NOW - timedelta(hours=3),
        "limite_resolucao": _NOW + timedelta(minutes=prazo_min),
        "resolvido_em": (_NOW - timedelta(hours=1)) if resolvido else None,
        "avaliacao_nota": nota, "avaliacao_comentario": coment,
        "avaliacao_em": _NOW if nota else None,
    }


_CHAMADOS = [
    _chamado("1", "BOND-2026-00412", "Falha no dosador da linha 3", "EM_ATENDIMENTO", "ALTA", "TI",
             cat="Equipamento", operador="Ana Lima", prazo_min=130),
    _chamado("2", "BOND-2026-00411", "Solicitação de acesso ao ERP", "AGUARDANDO", "MEDIA", "TI",
             cat="Acessos", operador="Bruno Sá", prazo_min=1700),
    _chamado("3", "BOND-2026-00409", "Vazamento em bombona — lote 8841", "NOVO", "URGENTE", "TI",
             cat="Segurança", prazo_min=-35),
    _chamado("4", "BOND-2026-00408", "Dúvida de diluição — BD CLEAN", "EM_ATENDIMENTO", "MEDIA", "RH",
             cat="Dúvida", operador="Ana Lima", prazo_min=380),
    _chamado("5", "BOND-2026-00405", "Reembolso de viagem", "RESOLVIDO", "BAIXA", "RH",
             cat="Financeiro", operador="Bruno Sá", resolvido=True, nota=5,
             coment="Atendimento rápido e claro, obrigado!"),
]
_POR_STATUS = {s: len([c for c in _CHAMADOS if c["status"] == s]) for s in STATUS_VALIDOS}

_MENSAGENS = [
    {"remetente_nome": "Marina Rocha", "created_at": _NOW - timedelta(hours=2, minutes=50),
     "is_interna": False, "conteudo": "Bom dia, o dosador parou de operar às 8h.", "anexos": []},
    {"remetente_nome": "Ana Lima", "created_at": _NOW - timedelta(hours=2, minutes=10),
     "is_interna": False, "conteudo": "Olá Marina, já estamos verificando. Pode enviar uma foto do painel?",
     "anexos": [{"nome": "painel-linha3.jpg", "url": "#"}]},
    {"remetente_nome": "Ana Lima", "created_at": _NOW - timedelta(hours=1, minutes=30),
     "is_interna": True, "conteudo": "Nota interna: acionar fornecedor do sensor (garantia).", "anexos": []},
]

_DEPARTAMENTOS = [
    {"id": "d1", "nome": "TI", "ativo": True},
    {"id": "d2", "nome": "RH", "ativo": True},
    {"id": "d3", "nome": "Marketing", "ativo": True},
]
_CATEGORIAS = [
    {"id": "c1", "nome": "Equipamento", "ativo": True},
    {"id": "c2", "nome": "Acessos", "ativo": True},
    {"id": "c3", "nome": "Dúvida", "ativo": False},
]
_PLANOS = [{"nome": "Padrão Interno", "resposta_alta_min": 120, "resolucao_alta_min": 1440,
            "resposta_default_min": 720, "resolucao_default_min": 1440, "ativo": True}]
_OPERADORES = [{"id": "o1", "nome": "Ana Lima", "departamento": "TI"},
               {"id": "o2", "nome": "Bruno Sá", "departamento": "RH"}]

_GRAFICOS = {
    "por_status": _POR_STATUS,
    "csat": {"1": 0, "2": 1, "3": 2, "4": 5, "5": 9},
    "por_departamento": [{"departamento": "TI", "total": 8},
                         {"departamento": "RH", "total": 3},
                         {"departamento": "Marketing", "total": 2}],
    "produtividade": [{"operador": "Ana Lima", "resolvidos": 6},
                      {"operador": "Bruno Sá", "resolvidos": 4}],
}
_KPIS = {"total": 25, "abertos": 6, "conformidade_sla": 92, "csat_media": 4.4,
         "csat_respostas": 17, "tma_horas": 8, "resolvidos": 19}


_LAUNCHER_GROUPS = [
    ("Login", "bg-navy-900", [("Tela de login", "/login")]),
    ("Cliente (funcionário)", "bg-brandgreen-600", [
        ("Meus chamados (dashboard)", "/portal"),
        ("Abrir chamado", "/portal/chamados/novo"),
        ("Detalhe do chamado", "/portal/chamados/1"),
    ]),
    ("Operador (workspace)", "bg-navy-500", [
        ("Fila de chamados", "/workspace"),
        ("Kanban", "/workspace/kanban"),
        ("Atendimento", "/workspace/chamados/1"),
    ]),
    ("Admin (TI)", "bg-brandgreen-700", [
        ("Indicadores (gráficos)", "/admin"),
        ("Gestão de catálogos", "/admin/gestao"),
    ]),
]


async def home(_):
    cards = ""
    for titulo, cor, telas in _LAUNCHER_GROUPS:
        links = "".join(
            f'<a href="{href}" class="flex items-center justify-between px-4 py-3 rounded-lg '
            f'ring-1 ring-line hover:ring-navy-500 hover:bg-surface transition text-sm font-medium text-navy">'
            f'{nome}<span class="text-faint">&rarr;</span></a>'
            for nome, href in telas
        )
        cards += (
            '<section class="bg-white rounded-xl shadow-card border border-line/60 p-5">'
            f'<div class="flex items-center gap-2.5 mb-4"><span class="w-8 h-8 rounded-lg {cor}"></span>'
            f'<h2 class="font-display font-bold text-navy">{titulo}</h2></div>'
            f'<div class="space-y-2">{links}</div></section>'
        )
    html = (
        '<!doctype html><html lang="pt-br"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Preview — Portal de Chamados Bondmann</title>'
        '<link rel="stylesheet" href="/static/css/app.css"></head>'
        '<body class="min-h-screen bg-surface text-ink font-sans antialiased">'
        '<div class="max-w-5xl mx-auto p-6 sm:p-10">'
        '<div class="flex items-center gap-2.5 mb-2">'
        '<svg viewBox="0 0 46 46" fill="none" stroke="currentColor" stroke-width="2.4" class="w-9 h-9 text-brandgreen">'
        '<circle cx="23" cy="23" r="5.3"/><circle cx="23" cy="10.5" r="5.3"/><circle cx="33.83" cy="16.75" r="5.3"/>'
        '<circle cx="33.83" cy="29.25" r="5.3"/><circle cx="23" cy="35.5" r="5.3"/><circle cx="12.17" cy="29.25" r="5.3"/>'
        '<circle cx="12.17" cy="16.75" r="5.3"/></svg>'
        '<div><div class="font-display font-black text-navy text-xl leading-none">BONDMANN</div>'
        '<div class="text-[9px] font-medium tracking-[0.42em] text-brandgreen">QUÍMICA</div></div></div>'
        '<h1 class="font-display font-bold text-2xl text-navy mt-4">Preview de telas</h1>'
        '<p class="text-muted text-sm mt-1 mb-6">Harness de desenvolvimento — dados de exemplo, sem banco. '
        'Escolha uma tela para inspecionar o visual.</p>'
        f'<div class="grid sm:grid-cols-2 gap-5">{cards}</div>'
        '</div></body></html>'
    )
    return HTMLResponse(html)


async def logout(_):      return RedirectResponse("/")
async def login_get(_):   return _r("login.html", erro=None, info=None)
async def login_post(_):  return RedirectResponse("/portal", status_code=303)


async def portal_dashboard(_):
    return _r("portal/dashboard.html",
              stats={"total": len(_CHAMADOS), "novo": _POR_STATUS["NOVO"],
                     "em_atendimento": _POR_STATUS["EM_ATENDIMENTO"], "resolvido": _POR_STATUS["RESOLVIDO"]},
              chamados=_CHAMADOS)


async def portal_novo(_):
    return _r("portal/novo_chamado.html", departamentos=_DEPARTAMENTOS,
              categorias=[c for c in _CATEGORIAS if c["ativo"]], prioridades=PRIORIDADES, erro=None)


async def portal_detalhe(_):
    return _r("portal/chamado_detalhe.html", chamado=_CHAMADOS[0], mensagens=_MENSAGENS,
              pode_avaliar=False, erro=None)


async def ws_fila(_):
    return _r("workspace/fila.html",
              stats={"total": len(_CHAMADOS), **_POR_STATUS}, filtro=None, chamados=_CHAMADOS)


async def ws_kanban(_):
    colunas = {s: [c for c in _CHAMADOS if c["status"] == s] for s in STATUS_VALIDOS}
    return _r("workspace/kanban.html", status_validos=STATUS_VALIDOS, colunas=colunas)


async def ws_atendimento(_):
    return _r("workspace/atendimento.html", chamado=_CHAMADOS[0], mensagens=_MENSAGENS,
              status_validos=STATUS_VALIDOS, prioridades=PRIORIDADES, operadores=_OPERADORES)


async def admin_dashboard(_):
    return _r("admin/dashboard.html", kpis=_KPIS, graficos=_GRAFICOS)


async def admin_gestao(_):
    return _r("admin/gestao.html", departamentos=_DEPARTAMENTOS, categorias=_CATEGORIAS, planos=_PLANOS)


async def admin_export(_):  return RedirectResponse("/admin")


routes = [
    Route("/", home),
    Route("/login", login_get),
    Route("/login", login_post, methods=["POST"]),
    Route("/logout", logout, methods=["GET", "POST"]),
    Route("/portal", portal_dashboard),
    Route("/portal/chamados/novo", portal_novo),
    Route("/portal/chamados/{cid}", portal_detalhe),
    Route("/workspace", ws_fila),
    Route("/workspace/kanban", ws_kanban),
    Route("/workspace/chamados/{cid}", ws_atendimento),
    Route("/admin", admin_dashboard),
    Route("/admin/gestao", admin_gestao),
    Route("/admin/export/csv", admin_export),
    Mount("/static", app=StaticFiles(directory=str(_STATIC)), name="static"),
]

app = Starlette(routes=routes)

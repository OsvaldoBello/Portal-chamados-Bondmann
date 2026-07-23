"""Configuração do Jinja2 (SSR) — autoescape sempre ligado (Seção 3.10)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.avatar_storage import avatar_public_url
from app.domain.sla_visual import barra_sla, estado_sla
from app.security.csrf import CSRF_HEADER, get_csrf

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"
_TZ = ZoneInfo("America/Sao_Paulo")  # exibição em horário de Brasília (Seção 5.2)


def static_url(path: str) -> str:
    """``/static/js/shell.js`` → ``/static/js/shell.js?v=<mtime>`` (cache-busting).

    Sem isso, o navegador de quem já visitou o site antes segue servindo do
    cache HTTP a versão ANTIGA de um `.js`/`.css` próprio (não os `vendor/*`,
    que são pinados por versão/SRI e imutáveis de propósito) mesmo depois do
    deploy trocar o conteúdo do arquivo no servidor — o HTML sempre vem
    fresco (SSR a cada request), mas o script/CSS referenciado por ele fica
    "grudado" na versão cacheada até um refresh forçado (bug real, 2026-07-23:
    lightbox de foto não abria pro usuário porque o `shell.js` antigo, sem a
    função, ainda estava em cache do navegador). ``mtime`` (não hash de
    conteúdo) é suficiente aqui: barato, sem I/O de leitura do arquivo
    inteiro, e muda a cada deploy que toca o arquivo. Arquivo ausente
    (ex.: erro de digitação do path) devolve a URL sem `?v=` — a 404
    resultante é mais fácil de depurar do que uma exceção aqui."""
    caminho = _STATIC_DIR / path.removeprefix("/static/")
    try:
        versao = int(caminho.stat().st_mtime)
    except OSError:
        return path
    return f"{path}?v={versao}"

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
    "AGUARDANDO_TERCEIROS": {"label": "Aguardando terceiros", "dot": "bg-st_terceiros", "text": "text-st_terceiros",
                       "bg": "bg-st_terceiros/10", "icon": "M15.75 5.25v13.5m-7.5-13.5v13.5"},
    "RESOLVIDO":      {"label": "Resolvido",      "dot": "bg-st_resolv", "text": "text-st_resolv",
                       "bg": "bg-st_resolv/10", "icon": "M4.5 12.75l6 6 9-13.5"},
}
PRIORIDADE_META = {
    "BAIXA":   {"label": "Baixa",   "text": "text-pr_baixa"},
    "MEDIA":   {"label": "Média",   "text": "text-pr_media"},
    "ALTA":    {"label": "Alta",    "text": "text-pr_alta"},
    "URGENTE": {"label": "Urgente", "text": "text-pr_urgente"},
}

# Paleta de rótulos (estilo Trello) para os badges de setor/categoria do Kanban.
# Cada valor de texto recebe uma cor estável (hash determinístico) — o mesmo
# setor/categoria sempre aparece na mesma cor entre cartões e sessões.
#
# ⚠️ As classes PRECISAM estar escritas literalmente aqui: o purge do Tailwind
# escaneia os .py (ver tailwind.config.js) e só mantém no bundle as classes que
# encontra como string literal. Não gere estas strings dinamicamente.
PALETA_ROTULOS = (
    "bg-rose-100 text-rose-800 ring-1 ring-inset ring-rose-600/20",
    "bg-orange-100 text-orange-800 ring-1 ring-inset ring-orange-600/20",
    "bg-amber-100 text-amber-800 ring-1 ring-inset ring-amber-600/20",
    "bg-lime-100 text-lime-800 ring-1 ring-inset ring-lime-600/20",
    "bg-green-100 text-green-800 ring-1 ring-inset ring-green-600/20",
    "bg-emerald-100 text-emerald-800 ring-1 ring-inset ring-emerald-600/20",
    "bg-teal-100 text-teal-800 ring-1 ring-inset ring-teal-600/20",
    "bg-cyan-100 text-cyan-800 ring-1 ring-inset ring-cyan-600/20",
    "bg-sky-100 text-sky-800 ring-1 ring-inset ring-sky-600/20",
    "bg-blue-100 text-blue-800 ring-1 ring-inset ring-blue-600/20",
    "bg-indigo-100 text-indigo-800 ring-1 ring-inset ring-indigo-600/20",
    "bg-violet-100 text-violet-800 ring-1 ring-inset ring-violet-600/20",
    "bg-fuchsia-100 text-fuchsia-800 ring-1 ring-inset ring-fuchsia-600/20",
    "bg-pink-100 text-pink-800 ring-1 ring-inset ring-pink-600/20",
)
_ROTULO_NEUTRO = "bg-navy-100 text-navy-800 ring-1 ring-inset ring-navy-500/20"


def cor_rotulo(texto: str | None) -> str:
    """Classes Tailwind (bg/text/ring) para um badge de rótulo, cor por texto.

    Determinístico e estável entre processos: usa MD5 do texto normalizado em
    vez de ``hash()`` (que é aleatorizado por processo). Rótulo vazio → neutro.
    """
    chave = (texto or "").strip().lower()
    if not chave:
        return _ROTULO_NEUTRO
    indice = int(hashlib.md5(chave.encode("utf-8")).hexdigest(), 16)
    return PALETA_ROTULOS[indice % len(PALETA_ROTULOS)]


def fmt_dt(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Formata um ``timestamptz`` (UTC no banco) em horário de Brasília."""
    if value is None:
        return "—"
    return value.astimezone(_TZ).strftime(fmt)


# Marcador de item de lista/tópico no início da linha: "-", "*", "•", "1.", "1)",
# "a)" etc. Um parágrafo com pelo menos uma linha assim é tratado como lista.
_MARCADOR_LISTA = re.compile(r"^\s*(?:[-*•‣·]|\d+[.)]|[a-zA-Z][.)])\s+")


def paragrafos_mensagem(texto: str | None) -> list[str]:
    """Quebra o texto de uma mensagem de chat em parágrafos de forma robusta.

    O conteúdo vem de fontes com hábitos diferentes de quebra de linha (1 Enter,
    vários Enters seguidos, colar de Word/WhatsApp com CRLF ou linhas "em branco"
    só com espaço) — qualquer sequência de 1+ linhas vazias vira UM separador de
    parágrafo.

    Dentro de um parágrafo, texto corrido que veio "quebrado na mão" (colado de
    um documento com largura fixa, uma linha por Enter) é rejuntado com espaço
    para o navegador reformatar de acordo com a largura real do chat — em vez de
    manter quebras que só faziam sentido na largura do documento original. Já um
    parágrafo com marcador de lista/tópico preserva as quebras como estão,
    porque cada linha é um item distinto (a renderização usa ``whitespace-pre-line``
    por parágrafo, então quebras preservadas continuam aparecendo como tal).
    """
    if not texto:
        return []
    linhas = texto.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocos: list[str] = []
    atual: list[str] = []

    def _fechar_bloco() -> None:
        if not atual:
            return
        limpas = [linha.strip() for linha in atual]
        if any(_MARCADOR_LISTA.match(linha) for linha in atual):
            blocos.append("\n".join(limpas).strip())
        else:
            blocos.append(" ".join(limpas).strip())
        atual.clear()

    for linha in linhas:
        if linha.strip() == "":
            _fechar_bloco()
        else:
            atual.append(linha)
    _fechar_bloco()
    return blocos


templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Jinja2Templates já liga autoescape para .html; reforçado explicitamente.
templates.env.autoescape = True
templates.env.globals.update(
    STATUS_META=STATUS_META,
    PRIORIDADE_META=PRIORIDADE_META,
    cor_rotulo=cor_rotulo,   # cor estável por setor/categoria no Kanban
    fmt_dt=fmt_dt,
    estado_sla=estado_sla,   # indicador visual de SLA (Fase 4)
    barra_sla=barra_sla,     # barra de progresso de SLA (Fase 3 do usuário)
    avatar_url=avatar_public_url,  # bolinha de avatar nos cards (Fase 7)
    paragrafos_mensagem=paragrafos_mensagem,  # quebra de parágrafo consistente no chat
    static_url=static_url,  # cache-busting de js/css próprio (2026-07-23)
)


def portal_base_template(perfil: dict | None) -> str:
    """Shell a estender pelas páginas do Portal (dashboard/abertura/detalhe/perfil).

    Staff (OPERADOR/ADMIN) nunca troca de shell: em vez de navegar para o shell
    do Portal (barra lateral com só 3 itens), continua no shell do Workspace —
    o item de menu correspondente só fica destacado (ver workspace_base.html).
    """
    if perfil and perfil.get("role") in ("OPERADOR", "ADMIN"):
        return "workspace/workspace_base.html"
    return "portal/app_base.html"


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

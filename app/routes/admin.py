"""Painel Admin & Relatórios (Fases 5 e 4) — indicadores por papel.

KPIs (TMA, conformidade de SLA, CSAT, produtividade), gestão de catálogos
(departamentos/categorias/planos) e export CSV.

**Acesso (decisão de produto 2026-07-09 — indicadores só do próprio setor):**
- **TI** (`auth_is_ti()` + role OPERADOR/ADMIN): vê os indicadores **apenas do
  próprio setor (TI)**, igual a qualquer outro departamento — não tem mais
  acesso a "Todos os setores" nem aos números de RH/Marketing. Continua sendo
  o único que **gere** catálogos/usuários/planos (isso é administração do
  sistema, não "indicador de outro setor").
- **ADMIN de departamento** (role ``ADMIN`` com ``departamento_id``, ex.: um
  gestor do RH): vê os indicadores **apenas do seu setor** (CSAT, SLA, rapidez,
  avaliações) — a RLS já escopa as queries ao departamento. **Não** gere
  catálogos (isso é do TI).

Gráficos com Chart.js (self-hosted, SRI); dados passados como JSON inerte
(`<script type="application/json">`) e lidos por `admin.js` (CSP-safe).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse, Response

from app import cache
from app.auth.dependencies import CurrentUser, enforce_admin_mfa, get_current_user
from app.avatar_storage import AvatarStorageError, avatar_path, enviar_avatar, preparar_avatar
from app.config import get_settings
from app.db import admin_connection, rls_request_scope
from app.repositories.admin import AdminRepo, get_admin_repo
from app.repositories.chamados import (
    CACHE_CATEGORIAS,
    CACHE_DEPARTAMENTOS,
    CACHE_SUBCATEGORIAS,
    ChamadosRepo,
    get_chamados_repo,
    validar_prazo_projeto,
)
from app.security.csrf import get_csrf
from app.security.password_policy import SENHA_MIN_CHARS
from app.security.uploads import UploadInvalido, validar_anexo
from app.services.admin import AdminService
from app.services.ingestao_quimico import ingerir_conn
from app.templating import render

router = APIRouter(prefix="/admin", tags=["admin"])

# Painel da base do Químico (F4): quem mantém a base é o próprio Químico.
_DEPTO_QUIMICO = "Dpto Químico"
# Limites de upload da reingestão (a planilha é pequena; o PDF de fichas ~6MB).
_MAX_PLANILHA_BYTES = 15 * 1024 * 1024
_MAX_PDF_BYTES = 30 * 1024 * 1024


@dataclass(frozen=True)
class AdminCtx:
    user: CurrentUser
    perfil: dict
    is_ti: bool
    escopo: str  # rótulo do escopo dos indicadores (setor ou "Todos os setores")
    # Item 3.3 (Fase 1): ADMIN que ainda não habilitou MFA — a UI avisa, não bloqueia.
    mfa_nudge: bool = False
    # 2026-07-23: TI, qualquer ADMIN de setor e o OPERADOR do Marketing podem
    # enviar/trocar a foto de perfil de usuários já criados (ver `usuarios()`
    # e `atualizar_avatar_usuario`) — sempre True para quem passa por
    # `admin_context` hoje, mas mantido explícito para não acoplar o gate de
    # avatar ao de acesso geral se este último ganhar mais grupos no futuro.
    pode_editar_avatares: bool = False
    # F4 (2026-07-24): staff do Dpto Químico (e o TI) pode manter a base de
    # conhecimento do agente Químico (`base_quimico_*`) pelo painel de upload —
    # ver `base_quimico`/`ingerir_base_quimico`.
    pode_editar_base_quimico: bool = False


async def admin_context(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Autoriza o acesso ao painel e resolve o **escopo** dos indicadores.

    Entra o **TI** (acesso total), o **ADMIN de departamento** (gestor do
    próprio setor) e, desde 2026-07-23, o **OPERADOR do Marketing** — este
    último só para poder enviar foto de perfil de usuários já criados
    (`_require_pode_editar_avatares`); as demais rotas seguem exigindo
    `_require_ti` ou `is_admin_dep`. Qualquer outro OPERADOR/CLIENTE recebe
    403. Um ADMIN sem setor não tem escopo de relatório → 403 (o único "admin
    global" é o TI).

    Dependência ``yield``: uma conexão RLS por request, reusada pelas queries
    (perfil, KPIs, catálogos, export) — performance (Seção 2.1)."""
    async with rls_request_scope(user.claims):
        perfil = await repo.perfil(user.claims)
        # is_ti aqui exige TAMBÉM ser staff (OPERADOR/ADMIN) — um CLIENTE do setor
        # TI (funcionário comum) não deve entrar no painel admin só por pertencer
        # ao departamento (perfil.is_ti é só "departamento = TI", sem olhar papel).
        is_ti = bool(
            perfil and perfil.get("is_ti") and perfil.get("role") in ("OPERADOR", "ADMIN")
        )
        is_admin_dep = bool(
            perfil and perfil.get("role") == "ADMIN" and perfil.get("departamento")
        )
        # Pedido do usuário (2026-07-23): OPERADOR do Marketing ganha uma porta de
        # entrada estreita no painel — só para poder tratar foto de perfil de
        # usuários já criados (ver migration 0052, mesma regra na RLS).
        is_mkt_operador = bool(
            perfil and perfil.get("role") == "OPERADOR" and perfil.get("departamento") == "Marketing"
        )
        # F4 (2026-07-24): staff do Químico (OPERADOR ou ADMIN do Dpto Químico)
        # ganha uma porta de entrada — só para manter a base do agente Químico
        # (`_require_base_quimico`); os demais recursos do painel seguem seus
        # próprios gates. Um ADMIN do Químico já entrava por `is_admin_dep`;
        # isto acrescenta o OPERADOR (analista) do setor.
        is_quimico_staff = bool(
            perfil
            and perfil.get("role") in ("OPERADOR", "ADMIN")
            and perfil.get("departamento") == _DEPTO_QUIMICO
        )
        if not (is_ti or is_admin_dep or is_mkt_operador or is_quimico_staff):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Área restrita a gestores (Admin do setor), TI, Operador do "
                "Marketing e staff do Químico.",
            )
        # MFA (item 3.3, Fase 1): o painel é a superfície de maior privilégio, então
        # é aqui que o aal2 é exigido do papel ADMIN. Levanta MfaChallengeRequired
        # (→ redirect para /mfa/verify) se o ADMIN tem MFA mas a sessão está em aal1;
        # devolve o nudge se ele ainda não habilitou. OPERADOR (TI ou Marketing),
        # que também entra aqui, fica de fora — nesta fatia o MFA só é imposto ao
        # papel ADMIN.
        mfa_nudge = enforce_admin_mfa(user, request)

        # Indicadores só do PRÓPRIO setor (decisão 2026-07-09) — TI passa a ser
        # escopado igual a qualquer Admin de departamento; "Todos os setores"
        # deixou de existir. TI segue sendo o único que GERE catálogos (_require_ti).
        escopo = perfil.get("departamento") or "—"
        yield AdminCtx(
            user=user, perfil=perfil, is_ti=is_ti, escopo=escopo, mfa_nudge=mfa_nudge,
            pode_editar_avatares=(is_ti or is_admin_dep or is_mkt_operador),
            pode_editar_base_quimico=(is_ti or is_quimico_staff),
        )


def _require_ti(ctx: AdminCtx) -> None:
    """Gestão de catálogos é exclusiva do TI (o Admin de setor só vê relatórios)."""
    if not ctx.is_ti:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Gestão de catálogos é restrita ao TI.",
        )


def _require_pode_editar_avatares(ctx: AdminCtx) -> None:
    """Ver/editar foto de perfil de usuários já criados: TI, ADMIN de qualquer
    setor ou OPERADOR do Marketing (2026-07-23) — quem passou por
    `admin_context` hoje sempre atende isso; a checagem existe para não
    depender implicitamente do gate de acesso geral."""
    if not ctx.pode_editar_avatares:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sem permissão para alterar fotos de perfil.",
        )


def _require_base_quimico(ctx: AdminCtx) -> None:
    """Manter a base do agente Químico (F4): TI ou staff (OPERADOR/ADMIN) do
    próprio Dpto Químico — ver `AdminCtx.pode_editar_base_quimico`."""
    if not ctx.pode_editar_base_quimico:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sem permissão para manter a base de conhecimento do Químico.",
        )


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _base_ctx(ctx: AdminCtx) -> dict:
    """Contexto comum aos templates do admin (perfil/escopo para o shell)."""
    return {
        "perfil": ctx.perfil,
        "is_ti": ctx.is_ti,
        "escopo": ctx.escopo,
        "mfa_nudge": ctx.mfa_nudge,
    }


def _parse_periodo(periodo: str) -> tuple[int, int]:
    """Aceita ``YYYY-MM`` (input type="month"); cai no mês corrente se ausente/
    inválido. Mesmo formato já usado por ``salvar_marketing_midia``."""
    if periodo:
        try:
            ano_s, mes_s = periodo.strip().split("-", 1)
            ano, mes = int(ano_s), int(mes_s)
            if 1 <= mes <= 12:
                return ano, mes
        except (ValueError, TypeError):
            pass
    hoje = datetime.now(UTC)
    return hoje.year, hoje.month


def _limites_periodo(ano: int, mes: int) -> tuple[datetime, datetime]:
    inicio = datetime(ano, mes, 1, tzinfo=UTC)
    fim = datetime(ano + 1, 1, 1, tzinfo=UTC) if mes == 12 else datetime(ano, mes + 1, 1, tzinfo=UTC)
    return inicio, fim


def _periodo_vizinho(ano: int, mes: int, *, delta: int) -> str:
    """``delta`` +1/-1 mês, devolvido já como ``YYYY-MM`` pro link do seletor."""
    novo_mes = mes + delta
    novo_ano = ano
    if novo_mes < 1:
        novo_mes, novo_ano = 12, ano - 1
    elif novo_mes > 12:
        novo_mes, novo_ano = 1, ano + 1
    return f"{novo_ano:04d}-{novo_mes:02d}"


@router.get("")
async def dashboard(
    request: Request,
    periodo: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    claims = ctx.user.claims

    # INDICADORES escopados ao PRÓPRIO setor sempre (2026-07-09) — TI incluído.
    # Nada de seletor "Todos os setores"/outro departamento: a RLS (rls_connection,
    # nunca admin_connection) já limita as queries ao departamento do usuário.
    dep_id = str(ctx.perfil.get("departamento_id") or "") or None
    escopo = ctx.escopo

    # Indicadores são mensais (2026-07-21): tudo escopado ao mês selecionado —
    # nunca escreve/apaga nada, é sempre um recorte por período em cima dos
    # chamados já existentes. Mês ausente/errado cai no atual. **2026-07-22:**
    # cada métrica ancora no seu próprio campo de data (AdminRepo.kpis) — volume
    # aberto por `created_at`, resolvidos/TMA por `resolvido_em`, CSAT por
    # `avaliacao_em` — não um único `created_at` pra tudo (um chamado aberto em
    # maio e fechado em junho conta pra junho nos indicadores de fechamento).
    ano, mes = _parse_periodo(periodo)
    periodo_inicio, periodo_fim = _limites_periodo(ano, mes)
    periodo_str = f"{ano:04d}-{mes:02d}"

    kpis = await repo.kpis(
        claims, departamento_id=dep_id, todos_setores=False,
        periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
    )
    graficos = {
        "por_status": await repo.por_status(
            claims, departamento_id=dep_id, todos_setores=False,
            periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
        ),
        "csat": await repo.csat_distribuicao(
            claims, departamento_id=dep_id, todos_setores=False,
            periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
        ),
        "por_departamento": await repo.por_departamento(
            claims, departamento_id=dep_id, todos_setores=False,
            periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
        ),
        "por_setor": await repo.por_setor(
            claims, departamento_id=dep_id, todos_setores=False,
            periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
        ),
        "produtividade": await repo.produtividade(
            claims, departamento_id=dep_id, todos_setores=False,
            periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
        ),
    }
    avaliacoes = await repo.avaliacoes_recentes(
        claims, departamento_id=dep_id, todos_setores=False,
        periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
    )

    if escopo == "Marketing":
        mkt_data = await repo.mkt_dashboard_data(claims)
        return render(
            request,
            "admin/dashboard_marketing.html",
            {
                **_base_ctx(ctx),
                "escopo": escopo,
                "departamentos": [],
                "departamento_sel": "",
                "mkt_data": mkt_data,
            },
        )

    return render(
        request,
        "admin/dashboard.html",
        {
            **_base_ctx(ctx),
            "escopo": escopo,
            "kpis": kpis,
            "graficos": graficos,
            "avaliacoes": avaliacoes,
            "departamentos": [],
            "departamento_sel": "",
            "periodo": periodo_str,
            "periodo_anterior": _periodo_vizinho(ano, mes, delta=-1),
            "periodo_seguinte": _periodo_vizinho(ano, mes, delta=1),
        },
    )


@router.get("/indicadores/avaliacoes")
async def avaliacoes_por_nota(
    request: Request,
    nota: int = 0,
    periodo: str = "",
    busca: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    """Fragmento HTMX: lista de chamados com uma nota de CSAT específica —
    conteúdo do modal aberto ao clicar numa barra de "Distribuição do CSAT"."""
    if nota not in (1, 2, 3, 4, 5):
        return render(request, "admin/_avaliacoes_lista.html", {"chamados": [], "nota": nota})
    dep_id = str(ctx.perfil.get("departamento_id") or "") or None
    ano, mes = _parse_periodo(periodo)
    periodo_inicio, periodo_fim = _limites_periodo(ano, mes)
    chamados = await repo.chamados_por_nota(
        ctx.user.claims, nota=nota, departamento_id=dep_id, todos_setores=False,
        busca=busca, periodo_inicio=periodo_inicio, periodo_fim=periodo_fim,
    )
    return render(request, "admin/_avaliacoes_lista.html", {"chamados": chamados, "nota": nota})


@router.get("/gestao")
async def gestao(
    request: Request,
    prioridade_ok: str = "",
    feriados_ok: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    _require_ti(ctx)
    categorias = await repo.categorias(ctx.user.claims)
    return render(
        request,
        "admin/gestao.html",
        {
            **_base_ctx(ctx),
            "departamentos": await repo.departamentos(ctx.user.claims),
            "categorias": categorias,
            "categorias_ativas": [c for c in categorias if c.get("ativo")],
            "subcategorias": await repo.subcategorias(ctx.user.claims),
            "planos": await repo.planos(ctx.user.claims),
            "prioridade_ok": prioridade_ok,
            "feriados_ok": feriados_ok,
            "midia_regional": await repo.marketing_midia_regional(ctx.user.claims),
        },
    )


@router.post("/marketing-midia")
async def salvar_marketing_midia(
    request: Request,
    mes: str = Form(...),               # "YYYY-MM" (input type="month")
    investimento: str = Form("0"),
    regioes: str = Form("0"),
    descontinuidades: str = Form("0"),
    aderencias: str = Form("0"),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Cria/atualiza um mês de Mídia Regional do Marketing (Fase 6 — 2026-07-09):
    qualquer mês agora entra por aqui, sem precisar de migration — é o que torna
    `marketing_midia_regional` dinâmica em vez de engessada nos 5 meses do seed
    original."""
    _require_ti(ctx)
    from datetime import date as _date

    mes = mes.strip()
    try:
        ano_s, mes_s = mes.split("-", 1)
        mes_data = _date(int(ano_s), int(mes_s), 1)
    except (ValueError, TypeError):
        return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)

    def _num(bruto: str, cast):
        try:
            return cast((bruto or "0").strip().replace(",", "."))
        except (ValueError, TypeError):
            return cast(0)

    await repo.upsert_marketing_midia_regional(
        ctx.user.claims,
        mes=mes_data,
        investimento=_num(investimento, float),
        regioes=_num(regioes, int),
        descontinuidades=_num(descontinuidades, int),
        aderencias=_num(aderencias, int),
    )
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/jobs/sincronizar-feriados")
async def job_sincronizar_feriados(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Sincroniza `feriados` com o calendário nacional calculado pela biblioteca
    `holidays` (Fase 5 — 2026-07-09), no lugar de editar a tabela à mão a cada
    virada de ano. `ON CONFLICT DO NOTHING`: nunca apaga/sobrescreve feriados
    locais já cadastrados manualmente. Só TI (mesmo escopo de `feriados_admin`
    na RLS)."""
    _require_ti(ctx)
    from datetime import datetime

    from app.domain.feriados import feriados_nacionais, proximos_anos

    anos = proximos_anos(datetime.now(UTC).year)
    novos = await repo.sincronizar_feriados(ctx.user.claims, feriados_nacionais(anos))
    return RedirectResponse(f"/admin/gestao?feriados_ok={novos}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/jobs/recalcular-prioridade-marketing")
async def job_recalcular_prioridade_marketing(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Recalcula a PRIORIDADE dos chamados do Marketing pelos dias restantes até
    `data_entrega` (Fase 4 — 2026-07-09): >7d Baixa, 4-7d Média, 2-3d Alta, <=1d
    (inclusive vencido) Urgente. Caminho automático diário é o `pg_cron`
    (`supabase/migrations/0031_prioridade_dinamica_marketing.sql`, mesma função
    SQL); este botão/rota serve para rodar sob demanda ou como alvo de um
    scheduler externo, caso `pg_cron` não esteja habilitado no projeto. Só TI
    (rotina de manutenção do sistema, não é indicador de setor)."""
    _require_ti(ctx)
    n = await repo.recalcular_prioridade_marketing(ctx.user.claims)
    return RedirectResponse(f"/admin/gestao?prioridade_ok={n}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/departamentos")
async def criar_departamento(
    request: Request,
    nome: str = Form(...),
    recebe_chamados: bool = Form(False),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    if nome:
        await repo.criar_departamento(ctx.user.claims, nome, recebe_chamados=recebe_chamados)
        cache.invalidate(CACHE_DEPARTAMENTOS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/departamentos/{dep_id}/toggle")
async def toggle_departamento(
    request: Request,
    dep_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    await repo.toggle_departamento(ctx.user.claims, dep_id)
    cache.invalidate(CACHE_DEPARTAMENTOS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/departamentos/{dep_id}/toggle-recebe")
async def toggle_recebe_departamento(
    request: Request,
    dep_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Liga/desliga se o setor recebe chamado (tem fila de atendimento) — só o
    catálogo de categorias e o roteamento de chamado usam esse flag."""
    _require_ti(ctx)
    await repo.toggle_recebe_departamento(ctx.user.claims, dep_id)
    cache.invalidate(CACHE_DEPARTAMENTOS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categorias")
async def criar_categoria(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
    departamento_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    # Categorias pertencem a um departamento (0019); valida o setor informado.
    # Categoria só faz sentido num setor com fila de atendimento (0027).
    dep_id = AdminService.departamento_valido(
        await repo.departamentos(ctx.user.claims), departamento_id, exigir_fila=True
    )
    if nome and dep_id:
        await repo.criar_categoria(ctx.user.claims, nome, descricao.strip() or None, dep_id)
        cache.invalidate_prefix(CACHE_CATEGORIAS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categorias/{cat_id}/toggle")
async def toggle_categoria(
    request: Request,
    cat_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    await repo.toggle_categoria(ctx.user.claims, cat_id)
    cache.invalidate_prefix(CACHE_CATEGORIAS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


def _invalidar_subcategorias(categoria_id: str | None) -> None:
    """Expurga o cache de subcategorias da categoria afetada (chave por-categoria)."""
    if categoria_id:
        cache.invalidate(f"{CACHE_SUBCATEGORIAS}:{categoria_id}")


@router.post("/subcategorias")
async def criar_subcategoria(
    request: Request,
    nome: str = Form(...),
    categoria_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    categoria_id = categoria_id.strip()
    # A categoria-mãe precisa existir/estar ativa (defesa em profundidade).
    valida = categoria_id and any(
        str(c["id"]) == categoria_id and c.get("ativo")
        for c in await repo.categorias(ctx.user.claims)
    )
    if nome and valida:
        await repo.criar_subcategoria(ctx.user.claims, categoria_id, nome)
        _invalidar_subcategorias(categoria_id)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/subcategorias/{sub_id}/toggle")
async def toggle_subcategoria(
    request: Request,
    sub_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    categoria_id = await repo.toggle_subcategoria(ctx.user.claims, sub_id)
    _invalidar_subcategorias(categoria_id)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


_PLANO_CAMPOS = (
    "resposta_baixa_min", "resposta_media_min", "resposta_alta_min",
    "resolucao_baixa_min", "resolucao_media_min", "resolucao_alta_min",
    "resposta_default_min", "resolucao_default_min",
)


@router.post("/planos/{plano_id}")
async def editar_plano(
    request: Request,
    plano_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Edita os tempos de SLA (minutos) de um plano. Só TI. Vale para os chamados
    criados a partir de agora (o trigger `calcular_sla_chamado` recalcula).

    `projeto_dias` (0066) vem no mesmo formulário mas em DIAS corridos: é o prazo
    padrão da coluna "Projetos" para quem não tiver prazo próprio no chamado.
    Valor inválido ou em branco mantém o que está gravado (a coluna é `NOT NULL`)."""
    _require_ti(ctx)
    form = await request.form()

    def _minutos(nome: str) -> int | None:
        bruto = (form.get(nome) or "").strip()
        if not bruto:
            return None
        try:
            n = int(bruto)
        except ValueError:
            return None
        return n if n >= 0 else None

    try:
        projeto_dias = validar_prazo_projeto(str(form.get("projeto_dias") or ""))
    except ValueError:
        projeto_dias = None

    campos = {c: _minutos(c) for c in _PLANO_CAMPOS}
    await repo.atualizar_plano(
        ctx.user.claims, plano_id, campos=campos, projeto_dias=projeto_dias
    )
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------
# Base de conhecimento do agente Químico (F4) — upload de planilha + PDF de
# fichas técnicas, reingestão determinística via app.services.ingestao_quimico
# (mesma lógica da CLI `scripts/ingestao_base_quimico.py`).
# --------------------------------------------------------------------------


@router.get("/base-quimico")
async def base_quimico(
    request: Request,
    ok: str = "",
    erro: str = "",
    ctx: AdminCtx = Depends(admin_context),
):
    _require_base_quimico(ctx)
    return render(
        request,
        "admin/base_quimico.html",
        {
            **_base_ctx(ctx),
            "ok": ok,
            "erro": erro,
            "relatorio": None,
            "max_planilha_mb": _MAX_PLANILHA_BYTES // (1024 * 1024),
            "max_pdf_mb": _MAX_PDF_BYTES // (1024 * 1024),
        },
    )


@router.post("/base-quimico")
async def ingerir_base_quimico(
    request: Request,
    planilha: UploadFile = File(...),
    fichas_pdf: UploadFile | None = File(None),
    remover_ausentes: bool = Form(False),
    ctx: AdminCtx = Depends(admin_context),
    _: None = Depends(_csrf_guard),
):
    """Sobe a planilha (+ PDF opcional) e faz upsert nas tabelas
    ``base_quimico_*`` numa ``admin_connection`` (bypassa RLS — a ingestão
    mexe em tabelas fora do escopo normal do usuário, mesmo do próprio
    químico). O parse é determinístico (openpyxl/pypdf, sem LLM — Regra de
    Ouro #4); as quantidades das formulações nunca passam por um modelo.

    Erro de validação simples (arquivo ausente/tipo errado) → redirect com
    ``erro=`` (padrão do painel). Sucesso renderiza a própria página com o
    relatório completo (contagens, fichas sem produto, produtos ausentes) —
    informação demais para caber numa querystring de redirect."""
    _require_base_quimico(ctx)

    def _volta(*, erro: str):
        from urllib.parse import urlencode

        qs = urlencode({"erro": erro})
        return RedirectResponse(f"/admin/base-quimico?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    if not planilha or not planilha.filename:
        return _volta(erro="Selecione a planilha (.xlsx).")

    try:
        bruto = await planilha.read(_MAX_PLANILHA_BYTES + 1)
        planilha_val = validar_anexo(planilha.filename, bruto, max_bytes=_MAX_PLANILHA_BYTES)
    except UploadInvalido as exc:
        return _volta(erro=f"Planilha inválida: {exc}")
    if planilha_val.ext != "xlsx":
        return _volta(erro="A planilha precisa ser um arquivo .xlsx.")

    pdf_bytes: bytes | None = None
    if fichas_pdf is not None and fichas_pdf.filename:
        try:
            bruto_pdf = await fichas_pdf.read(_MAX_PDF_BYTES + 1)
            pdf_val = validar_anexo(fichas_pdf.filename, bruto_pdf, max_bytes=_MAX_PDF_BYTES)
        except UploadInvalido as exc:
            return _volta(erro=f"PDF de fichas inválido: {exc}")
        if pdf_val.ext != "pdf":
            return _volta(erro="O arquivo de fichas técnicas precisa ser um .pdf.")
        pdf_bytes = pdf_val.conteudo

    try:
        async with admin_connection() as conn:
            relatorio = await ingerir_conn(
                conn, planilha_val.conteudo, pdf_bytes, remover_ausentes=remover_ausentes
            )
    except KeyError as exc:
        return _volta(erro=f"Planilha fora do padrão esperado — aba ausente: {exc}.")
    except Exception:  # noqa: BLE001 — parse/formato inesperado: mensagem genérica ao usuário
        return _volta(erro="Não foi possível interpretar os arquivos. Confira o formato e tente novamente.")

    return render(
        request,
        "admin/base_quimico.html",
        {
            **_base_ctx(ctx),
            "ok": "Base atualizada com sucesso.",
            "erro": "",
            "relatorio": relatorio,
            "max_planilha_mb": _MAX_PLANILHA_BYTES // (1024 * 1024),
            "max_pdf_mb": _MAX_PDF_BYTES // (1024 * 1024),
        },
    )


# --------------------------------------------------------------------------
# Gestão de contas (criar/promover usuários) — exclusivo do TI.
# --------------------------------------------------------------------------
_PAPEIS = {"CLIENTE", "OPERADOR", "ADMIN"}
# Rótulos amigáveis (o papel interno CLIENTE aparece como "Funcionário" na UI).
_PAPEL_LABEL = {"CLIENTE": "Funcionário", "OPERADOR": "Operador", "ADMIN": "Admin de setor"}


async def _emails_por_id() -> dict[str, str]:
    """Mapa ``{user_id: email}`` via Admin API (service_role). O e-mail vive em
    ``auth.users``, fora do alcance do papel ``authenticated`` — por isso vem daqui.
    Degrada para ``{}`` se a service_role não estiver configurada.

    ``list_users`` é paginado pelo GoTrue (50 por página, por padrão) — sem
    percorrer todas as páginas, usuários além da primeira ficam sem e-mail na
    tela (bug observado com a base atual de usuários)."""
    from app.auth.supabase_client import ensure_admin_client

    client = await ensure_admin_client()
    if client is None:
        return {}
    emails: dict[str, str] = {}
    pagina = 1
    try:
        while True:
            usuarios = await client.auth.admin.list_users(page=pagina, per_page=200)
            itens = getattr(usuarios, "users", usuarios) or []
            if not itens:
                break
            for u in itens:
                emails[str(u.id)] = getattr(u, "email", None) or ""
            if len(itens) < 200:
                break
            pagina += 1
    except Exception:  # noqa: BLE001 — Admin API indisponível → sem e-mails
        return emails
    return emails


@router.get("/usuarios")
async def usuarios(
    request: Request,
    ok: str = "",
    erro: str = "",
    confirmar: str = "",
    busca: str = "",
    papel: str = "",
    setor: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    _require_pode_editar_avatares(ctx)
    from app.auth.supabase_client import ensure_admin_client

    lista = await repo.usuarios(ctx.user.claims)
    emails = await _emails_por_id()
    for u in lista:
        u["email"] = emails.get(str(u["id"]), "")

    busca_norm = busca.strip().lower()
    papel_sel = papel.strip() if papel.strip() in _PAPEIS else ""
    setor_sel = setor.strip()
    if busca_norm:
        lista = [
            u for u in lista
            if busca_norm in (u.get("nome") or "").lower()
            or busca_norm in (u.get("email") or "").lower()
        ]
    if papel_sel:
        lista = [u for u in lista if u["role"] == papel_sel]
    if setor_sel:
        lista = [u for u in lista if str(u.get("departamento_id") or "") == setor_sel]

    return render(
        request,
        "admin/usuarios.html",
        {
            **_base_ctx(ctx),
            "usuarios": lista,
            # Setor de origem (0028): todo usuário tem um, então TODOS os
            # departamentos ativos entram aqui (não só os que recebem chamado).
            "departamentos": [d for d in await repo.departamentos(ctx.user.claims) if d.get("ativo")],
            "admin_disponivel": (await ensure_admin_client()) is not None,
            "ok": ok,
            "erro": erro,
            # Exclusão em 2 etapas: ``confirmar`` = id do usuário aguardando confirmação.
            "confirmar": confirmar.strip(),
            "busca_sel": busca,
            "papel_sel": papel_sel,
            "setor_sel": setor_sel,
            "tem_filtro_usuarios": bool(busca_norm or papel_sel or setor_sel),
        },
    )


@router.post("/usuarios")
async def criar_usuario(
    request: Request,
    background_tasks: BackgroundTasks,
    nome: str = Form(""),
    email: str = Form(...),
    senha: str = Form(...),
    papel: str = Form("CLIENTE"),
    departamento_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Cria a conta (GoTrue Admin API) já confirmada e promove papel + setor
    (todo usuário tem um setor de origem — 0028). Dual-write: app_metadata.role
    (JWT) + perfis (RLS)."""
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    email = email.strip().lower()
    nome = nome.strip() or email
    papel = papel.strip().upper()
    if papel not in _PAPEIS:
        papel = "CLIENTE"
    if len(senha) < SENHA_MIN_CHARS:
        return _volta(erro=f"A senha deve ter ao menos {SENHA_MIN_CHARS} caracteres.")
    # Setor de origem obrigatório pra qualquer papel (0028) — inclusive
    # Funcionário: é o que permite o líder do setor ver os chamados da equipe.
    # Só OPERADOR exige setor com fila (é quem atende, 0028).
    dep_id = AdminService.departamento_valido(
        await repo.departamentos(ctx.user.claims), departamento_id, exigir_fila=(papel == "OPERADOR")
    )
    if dep_id is None:
        return _volta(erro="Selecione o departamento do usuário.")

    client = await ensure_admin_client()
    if client is None:
        return _volta(erro="Criação indisponível: service_role não configurada no servidor.")

    try:
        resp = await client.auth.admin.create_user(
            {
                "email": email,
                "password": senha,
                "email_confirm": True,
                "user_metadata": {"nome": nome},
                "app_metadata": {"role": papel},
            }
        )
    except Exception as exc:  # noqa: BLE001 — e-mail duplicado / erro do GoTrue
        return _volta(erro=f"Não foi possível criar: {type(exc).__name__} (e-mail já existe?).")

    novo = getattr(resp, "user", None)
    if novo is None:
        return _volta(erro="Conta não criada (resposta inesperada do Supabase).")

    # O trigger criou o perfil CLIENTE sem setor; sempre promovemos papel+setor.
    await repo.atualizar_papel(ctx.user.claims, str(novo.id), role=papel, departamento_id=dep_id)

    # Boas-vindas por e-mail (2026-07-21): instrui o primeiro acesso via
    # "Esqueci minha senha" — em background pra não atrasar a resposta (mesmo
    # padrão de app.notification.agendar_notificacao_email).
    from app.notification import notificar_novo_usuario_email

    background_tasks.add_task(notificar_novo_usuario_email, nome, email)

    # Foto de perfil (opcional, Fase 7): o TI já pode enviá-la na criação da
    # conta. O upload usa a service_role key (não o JWT do TI) porque a policy
    # de Storage só libera cada um escrever no PRÓPRIO path — aqui o alvo é o
    # path do usuário recém-criado, então é preciso bypassar a RLS.
    form_data = await request.form()
    avatar = form_data.get("avatar")
    aviso_avatar = ""
    if avatar is not None and getattr(avatar, "filename", ""):
        settings = get_settings()
        conteudo = await avatar.read(settings.avatar_max_bytes + 1)
        try:
            processada = preparar_avatar(avatar.filename, conteudo, max_bytes=settings.avatar_max_bytes)
            path = avatar_path(str(novo.id), "png")
            await enviar_avatar(
                settings.supabase_service_role_key, path, processada, "image/png"
            )
            await repo.atualizar_avatar(ctx.user.claims, str(novo.id), avatar_path=path)
        except (UploadInvalido, AvatarStorageError) as exc:
            aviso_avatar = f" (a foto não foi salva: {exc})"

    return _volta(ok=f"Conta {email} criada como {_PAPEL_LABEL[papel]}.{aviso_avatar}")


@router.post("/usuarios/{user_id}/avatar")
async def atualizar_avatar_usuario(
    request: Request,
    user_id: str,
    arquivo: UploadFile = File(...),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Envia/substitui a foto de perfil de um usuário JÁ CRIADO (2026-07-23):
    pedido do usuário — além do TI, qualquer ADMIN de setor e o OPERADOR do
    Marketing podem fazer isso para QUALQUER usuário, não só na criação da
    conta. `_require_pode_editar_avatares` é o gate de app; a RLS (migration
    0052, mesma regra) é quem garante isso também no banco, caso essa checagem
    de app tenha algum furo. Upload no Storage via service_role: a policy de
    `storage.objects` só libera cada um escrever no PRÓPRIO path (0033), então
    o alvo aqui (path de OUTRO usuário) precisa bypassar a RLS de Storage —
    mesmo padrão já usado em `criar_usuario`."""
    _require_pode_editar_avatares(ctx)

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    if not arquivo or not arquivo.filename:
        return _volta(erro="Selecione uma imagem.")

    settings = get_settings()
    if not settings.supabase_service_role_key:
        return _volta(erro="Upload indisponível: service_role não configurada no servidor.")

    conteudo = await arquivo.read(settings.avatar_max_bytes + 1)
    try:
        processada = preparar_avatar(arquivo.filename, conteudo, max_bytes=settings.avatar_max_bytes)
    except UploadInvalido as exc:
        return _volta(erro=str(exc))

    path = avatar_path(user_id, "png")
    try:
        await enviar_avatar(settings.supabase_service_role_key, path, processada, "image/png")
    except AvatarStorageError:
        return _volta(erro="Falha ao enviar a foto. Tente novamente.")

    await repo.atualizar_avatar(ctx.user.claims, user_id, avatar_path=path)
    return _volta(ok="Foto de perfil atualizada.")


@router.post("/usuarios/{user_id}/papel")
async def mudar_papel(
    request: Request,
    user_id: str,
    papel: str = Form("CLIENTE"),
    departamento_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Altera papel + setor de um usuário existente. Dual-write: perfis (RLS) e
    app_metadata.role (Admin API). A mudança do JWT vale no próximo login/refresh."""
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    papel = papel.strip().upper()
    if papel not in _PAPEIS:
        return _volta(erro="Papel inválido.")
    dep_id = AdminService.departamento_valido(
        await repo.departamentos(ctx.user.claims), departamento_id, exigir_fila=(papel == "OPERADOR")
    )
    if dep_id is None:
        return _volta(erro="Selecione o departamento do usuário.")

    client = await ensure_admin_client()
    resultado = await AdminService.promover_papel(
        repo=repo, claims=ctx.user.claims, user_id=user_id, papel=papel,
        departamento_id=dep_id, client=client,
    )
    if not resultado.sucesso:
        return _volta(erro=resultado.mensagem)
    return _volta(ok=resultado.mensagem)


@router.post("/usuarios/{user_id}/reset-mfa")
async def reset_mfa(
    request: Request,
    user_id: str,
    ctx: AdminCtx = Depends(admin_context),
    _: None = Depends(_csrf_guard),
):
    """Remove o MFA de um usuário — **recovery por admin** (decisão do gestor,
    item 3.3): quem perde o autenticador pede ao TI para resetar e re-enrola.

    Não há recovery codes guardados por nós (seriam mais um segredo sob nossa
    guarda); o segredo TOTP vive só no GoTrue. Remover um fator verificado desloga
    o usuário de todas as sessões — efeito do próprio GoTrue. Só TI."""
    from app.auth import mfa as mfa_ops

    _require_ti(ctx)

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    try:
        removidos = await mfa_ops.resetar_mfa(user_id)
    except mfa_ops.MfaErro as exc:
        return _volta(erro=f"Não foi possível resetar o MFA: {exc}")
    if not removidos:
        return _volta(ok="Este usuário não tinha MFA configurado.")
    return _volta(ok="MFA resetado. O usuário poderá configurá-lo de novo no próximo acesso.")


@router.post("/usuarios/{user_id}/excluir")
async def excluir_usuario(
    request: Request,
    user_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Exclui a conta (GoTrue Admin API). A remoção de ``auth.users`` cascateia
    para ``perfis``; se o usuário tiver chamados/mensagens (FK RESTRICT), a
    exclusão falha e sugerimos desativar em vez de excluir. Só TI."""
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    if str(user_id) == str(ctx.user.id):
        return _volta(erro="Você não pode excluir a própria conta.")

    client = await ensure_admin_client()
    if client is None:
        return _volta(erro="Exclusão indisponível: service_role não configurada no servidor.")

    try:
        await client.auth.admin.delete_user(user_id)
    except Exception:  # noqa: BLE001 — FK RESTRICT (tem chamados/mensagens) ou erro do GoTrue
        return _volta(
            erro="Não foi possível excluir: o usuário tem chamados ou mensagens no "
                 "histórico. Reatribua/encerre esses itens ou apenas não use a conta."
        )
    return _volta(ok="Usuário excluído.")


_CSV_COLS = [
    "codigo", "titulo", "descricao", "status", "prioridade", "departamento",
    "categoria", "subcategoria", "solicitante", "operador", "created_at",
    "limite_resolucao", "respondido_em", "resolvido_em", "avaliacao_nota",
    "avaliacao_em", "avaliacao_comentario", "combinado_com",
]

# Cabeçalho legível (pt-BR) — mesmo padrão de planilha de relatório usado pela
# empresa: nomes de coluna por extenso em vez das chaves internas do banco.
_CSV_HEADERS = {
    "codigo": "Chamado",
    "titulo": "Título",
    "descricao": "Descrição",
    "status": "Status",
    "prioridade": "Prioridade",
    "departamento": "Departamento",
    "categoria": "Categoria",
    "subcategoria": "Subcategoria",
    "solicitante": "Solicitante",
    "operador": "Operador",
    "created_at": "Data de criação",
    "limite_resolucao": "Prazo de resolução",
    "respondido_em": "Respondido em",
    "resolvido_em": "Resolvido em",
    "avaliacao_nota": "Avaliação",
    "avaliacao_em": "Avaliado em",
    "avaliacao_comentario": "Comentário da avaliação",
    # Combinação de chamados (0065): vazio = chamado normal; preenchido = este
    # chamado é duplicado do código indicado e NÃO conta nos indicadores.
    "combinado_com": "Combinado com",
}


@router.get("/export/csv")
async def export_csv(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    from app.templating import fmt_dt

    linhas = await repo.exportar(ctx.user.claims)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=_CSV_COLS, extrasaction="ignore", delimiter=";"
    )
    writer.writerow(_CSV_HEADERS)
    for r in linhas:
        writer.writerow(
            {
                k: (fmt_dt(v, "%d/%m/%Y %H:%M") if isinstance(v, datetime) else (v if v is not None else ""))
                for k, v in r.items()
            }
        )
    nome = f"chamados_{datetime.now(UTC):%Y%m%d}.csv"
    # BOM UTF-8: sem ele o Excel (padrão pt-BR) lê acentos como lixo (mojibake).
    # ";" como delimitador é o padrão de CSV que o Excel pt-BR abre já separado
    # em colunas (o separador decimal "," do locale conflita com "," de campo).
    conteudo = "﻿" + buf.getvalue()
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def register_admin_routes(app) -> None:
    app.include_router(router)

"""Painel Admin & Relatórios (Fases 5 e 4) — indicadores por papel.

KPIs (TMA, conformidade de SLA, CSAT, produtividade), gestão de catálogos
(departamentos/categorias/planos) e export CSV.

**Acesso (Fase 4 — papéis por departamento):**
- **TI** (`auth_is_ti()`): acesso total — vê os indicadores de **todos** os
  departamentos e pode **gerir** catálogos.
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
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from app import cache
from app.auth.dependencies import CurrentUser, get_current_user
from app.db import rls_request_scope
from app.repositories.admin import AdminRepo, get_admin_repo
from app.repositories.chamados import (
    CACHE_CATEGORIAS,
    CACHE_DEPARTAMENTOS,
    ChamadosRepo,
    get_chamados_repo,
)
from app.security.csrf import get_csrf
from app.templating import render

router = APIRouter(prefix="/admin", tags=["admin"])


@dataclass(frozen=True)
class AdminCtx:
    user: CurrentUser
    perfil: dict
    is_ti: bool
    escopo: str  # rótulo do escopo dos indicadores (setor ou "Todos os setores")


async def admin_context(
    user: CurrentUser = Depends(get_current_user),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Autoriza o acesso ao painel e resolve o **escopo** dos indicadores.

    Entra o **TI** (acesso total) e o **ADMIN de departamento** (gestor do
    próprio setor). OPERADOR/CLIENTE recebem 403. Um ADMIN sem setor não tem
    escopo de relatório → 403 (o único "admin global" é o TI).

    Dependência ``yield``: uma conexão RLS por request, reusada pelas queries
    (perfil, KPIs, catálogos, export) — performance (Seção 2.1)."""
    async with rls_request_scope(user.claims):
        perfil = await repo.perfil(user.claims)
        is_ti = bool(perfil and perfil.get("is_ti"))
        is_admin_dep = bool(
            perfil and perfil.get("role") == "ADMIN" and perfil.get("departamento")
        )
        if not (is_ti or is_admin_dep):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Área restrita a gestores (Admin do setor) e ao TI.",
            )
        escopo = "Todos os setores" if is_ti else perfil["departamento"]
        yield AdminCtx(user=user, perfil=perfil, is_ti=is_ti, escopo=escopo)


def _require_ti(ctx: AdminCtx) -> None:
    """Gestão de catálogos é exclusiva do TI (o Admin de setor só vê relatórios)."""
    if not ctx.is_ti:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Gestão de catálogos é restrita ao TI.",
        )


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _base_ctx(ctx: AdminCtx) -> dict:
    """Contexto comum aos templates do admin (perfil/escopo para o shell)."""
    return {"perfil": ctx.perfil, "is_ti": ctx.is_ti, "escopo": ctx.escopo}


@router.get("")
async def dashboard(
    request: Request,
    departamento: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    claims = ctx.user.claims

    # Filtro por departamento é exclusivo do TI (que enxerga todos os setores via
    # RLS). O Admin de setor já é escopado pela RLS ao seu próprio departamento —
    # para ele o seletor não se aplica.
    departamentos: list[dict] = []
    dep_id: str | None = None
    dep_nome: str | None = None
    if ctx.is_ti:
        departamentos = [
            d for d in await repo.departamentos(claims) if d.get("ativo")
        ]
        escolhido = departamento.strip()
        selecionado = next(
            (d for d in departamentos if str(d["id"]) == escolhido), None
        )
        if selecionado:
            dep_id = str(selecionado["id"])
            dep_nome = selecionado["nome"]

    kpis = await repo.kpis(claims, departamento_id=dep_id)
    graficos = {
        "por_status": await repo.por_status(claims, departamento_id=dep_id),
        "csat": await repo.csat_distribuicao(claims, departamento_id=dep_id),
        "por_departamento": await repo.por_departamento(claims, departamento_id=dep_id),
        "produtividade": await repo.produtividade(claims, departamento_id=dep_id),
    }
    avaliacoes = await repo.avaliacoes_recentes(claims, departamento_id=dep_id)
    escopo = dep_nome or ctx.escopo
    return render(
        request,
        "admin/dashboard.html",
        {
            **_base_ctx(ctx),
            "escopo": escopo,
            "kpis": kpis,
            "graficos": graficos,
            "avaliacoes": avaliacoes,
            "departamentos": departamentos,
            "departamento_sel": dep_id or "",
        },
    )


@router.get("/gestao")
async def gestao(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    _require_ti(ctx)
    return render(
        request,
        "admin/gestao.html",
        {
            **_base_ctx(ctx),
            "departamentos": await repo.departamentos(ctx.user.claims),
            "categorias": await repo.categorias(ctx.user.claims),
            "planos": await repo.planos(ctx.user.claims),
        },
    )


@router.post("/departamentos")
async def criar_departamento(
    request: Request,
    nome: str = Form(...),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    if nome:
        await repo.criar_departamento(ctx.user.claims, nome)
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


@router.post("/categorias")
async def criar_categoria(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    if nome:
        await repo.criar_categoria(ctx.user.claims, nome, descricao.strip() or None)
        cache.invalidate(CACHE_CATEGORIAS)
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
    cache.invalidate(CACHE_CATEGORIAS)
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
    criados a partir de agora (o trigger `calcular_sla_chamado` recalcula)."""
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

    campos = {c: _minutos(c) for c in _PLANO_CAMPOS}
    await repo.atualizar_plano(ctx.user.claims, plano_id, campos=campos)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------
# Gestão de contas (criar/promover usuários) — exclusivo do TI.
# --------------------------------------------------------------------------
_PAPEIS = {"CLIENTE", "OPERADOR", "ADMIN"}
_SENHA_MIN = 8


async def _emails_por_id() -> dict[str, str]:
    """Mapa ``{user_id: email}`` via Admin API (service_role). O e-mail vive em
    ``auth.users``, fora do alcance do papel ``authenticated`` — por isso vem daqui.
    Degrada para ``{}`` se a service_role não estiver configurada."""
    from app.auth.supabase_client import ensure_admin_client

    client = await ensure_admin_client()
    if client is None:
        return {}
    try:
        usuarios = await client.auth.admin.list_users()
    except Exception:  # noqa: BLE001 — Admin API indisponível → sem e-mails
        return {}
    itens = getattr(usuarios, "users", usuarios) or []
    return {str(u.id): (getattr(u, "email", None) or "") for u in itens}


async def _depto_valido(repo: AdminRepo, claims: dict, dep_id: str) -> str | None:
    """Retorna o id do departamento se existir e estiver ativo; senão ``None``."""
    dep_id = (dep_id or "").strip()
    if not dep_id:
        return None
    for d in await repo.departamentos(claims):
        if str(d["id"]) == dep_id and d.get("ativo"):
            return dep_id
    return None


@router.get("/usuarios")
async def usuarios(
    request: Request,
    ok: str = "",
    erro: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    lista = await repo.usuarios(ctx.user.claims)
    emails = await _emails_por_id()
    for u in lista:
        u["email"] = emails.get(str(u["id"]), "")
    return render(
        request,
        "admin/usuarios.html",
        {
            **_base_ctx(ctx),
            "usuarios": lista,
            "departamentos": [d for d in await repo.departamentos(ctx.user.claims) if d.get("ativo")],
            "admin_disponivel": (await ensure_admin_client()) is not None,
            "ok": ok,
            "erro": erro,
        },
    )


@router.post("/usuarios")
async def criar_usuario(
    request: Request,
    nome: str = Form(""),
    email: str = Form(...),
    senha: str = Form(...),
    papel: str = Form("CLIENTE"),
    departamento_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Cria a conta (GoTrue Admin API) já confirmada e, se for staff, promove o
    papel + setor. Dual-write: app_metadata.role (JWT) + perfis (RLS)."""
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
    if len(senha) < _SENHA_MIN:
        return _volta(erro=f"A senha deve ter ao menos {_SENHA_MIN} caracteres.")
    dep_id = await _depto_valido(repo, ctx.user.claims, departamento_id)
    if papel in ("OPERADOR", "ADMIN") and dep_id is None:
        return _volta(erro="Selecione um departamento para operador/admin de setor.")
    if papel == "CLIENTE":
        dep_id = None  # funcionário não tem setor

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

    # O trigger criou o perfil CLIENTE; promovemos papel/setor se for staff.
    if papel != "CLIENTE" or dep_id is not None:
        await repo.atualizar_papel(ctx.user.claims, str(novo.id), role=papel, departamento_id=dep_id)

    return _volta(ok=f"Conta {email} criada como {papel}.")


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
    dep_id = await _depto_valido(repo, ctx.user.claims, departamento_id)
    if papel in ("OPERADOR", "ADMIN") and dep_id is None:
        return _volta(erro="Selecione um departamento para operador/admin de setor.")
    if papel == "CLIENTE":
        dep_id = None

    await repo.atualizar_papel(ctx.user.claims, user_id, role=papel, departamento_id=dep_id)

    # Espelha o papel no JWT (app_metadata) — senão o app trata pelo papel antigo.
    client = await ensure_admin_client()
    if client is not None:
        try:
            await client.auth.admin.update_user_by_id(user_id, {"app_metadata": {"role": papel}})
        except Exception:  # noqa: BLE001 — perfis já atualizado; JWT sincroniza no próximo login
            return _volta(ok="Papel atualizado no banco. Peça re-login (JWT sincroniza depois).")
    return _volta(ok="Papel atualizado. A mudança vale no próximo login do usuário.")


_CSV_COLS = [
    "codigo", "titulo", "status", "prioridade", "departamento", "categoria",
    "solicitante", "operador", "created_at", "limite_resolucao",
    "respondido_em", "resolvido_em", "avaliacao_nota", "avaliacao_em",
    "avaliacao_comentario",
]


@router.get("/export/csv")
async def export_csv(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    linhas = await repo.exportar(ctx.user.claims)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_COLS, extrasaction="ignore")
    writer.writeheader()
    for r in linhas:
        writer.writerow({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()})
    nome = f"chamados_{datetime.utcnow():%Y%m%d}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def register_admin_routes(app) -> None:
    app.include_router(router)

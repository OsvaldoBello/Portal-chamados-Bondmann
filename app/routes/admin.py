"""Painel Admin & Relatórios (Fase 5) — restrito ao departamento TI.

KPIs (TMA, conformidade de SLA, CSAT, produtividade), gestão de catálogos
(departamentos/categorias/planos) e export CSV. Acesso: `auth_is_ti()`.
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

from app.auth.dependencies import CurrentUser, get_current_user
from app.repositories.admin import AdminRepo, get_admin_repo
from app.security.csrf import get_csrf
from app.templating import render

router = APIRouter(prefix="/admin", tags=["admin"])


@dataclass(frozen=True)
class AdminCtx:
    user: CurrentUser


async def admin_context(
    user: CurrentUser = Depends(get_current_user),
    repo: AdminRepo = Depends(get_admin_repo),
) -> AdminCtx:
    """Exige que o usuário seja do departamento TI (acesso total)."""
    if not await repo.is_ti(user.claims):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Área restrita ao departamento TI.",
        )
    return AdminCtx(user=user)


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


@router.get("")
async def dashboard(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    claims = ctx.user.claims
    kpis = await repo.kpis(claims)
    graficos = {
        "por_status": await repo.por_status(claims),
        "csat": await repo.csat_distribuicao(claims),
        "por_departamento": await repo.por_departamento(claims),
        "produtividade": await repo.produtividade(claims),
    }
    return render(request, "admin/dashboard.html", {"kpis": kpis, "graficos": graficos})


@router.get("/gestao")
async def gestao(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    return render(
        request,
        "admin/gestao.html",
        {
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
    nome = nome.strip()
    if nome:
        await repo.criar_departamento(ctx.user.claims, nome)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/departamentos/{dep_id}/toggle")
async def toggle_departamento(
    request: Request,
    dep_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    await repo.toggle_departamento(ctx.user.claims, dep_id)
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
    nome = nome.strip()
    if nome:
        await repo.criar_categoria(ctx.user.claims, nome, descricao.strip() or None)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categorias/{cat_id}/toggle")
async def toggle_categoria(
    request: Request,
    cat_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    await repo.toggle_categoria(ctx.user.claims, cat_id)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


_CSV_COLS = [
    "codigo", "titulo", "status", "prioridade", "departamento", "categoria",
    "solicitante", "operador", "created_at", "limite_resolucao",
    "respondido_em", "resolvido_em", "avaliacao_nota",
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

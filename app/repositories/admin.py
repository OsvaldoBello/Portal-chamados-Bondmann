"""Repositório do Painel Admin (Fase 5) — KPIs, gestão de catálogos e export.

Acesso restrito ao departamento **TI** (acesso total). As consultas rodam sob
RLS com os claims do usuário; como o admin é TI (`auth_is_ti()` = true), a RLS
devolve a visão completa. Gestão de departamentos/categorias/planos é permitida
pelas policies `*_admin_all` (também `auth_is_ti()`).
"""

from __future__ import annotations

from typing import Any

from app.db import rls_connection


class AdminRepo:
    async def is_ti(self, claims: dict) -> bool:
        async with rls_connection(claims) as conn:
            return bool(await conn.fetchval("SELECT auth_is_ti()"))

    # ---- KPIs -----------------------------------------------------------
    async def kpis(self, claims: dict) -> dict[str, Any]:
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  count(*)                                             AS total,
                  count(*) FILTER (WHERE status <> 'RESOLVIDO')        AS abertos,
                  count(*) FILTER (WHERE resolvido_em IS NOT NULL)     AS resolvidos,
                  count(*) FILTER (
                    WHERE resolvido_em IS NOT NULL AND limite_resolucao IS NOT NULL
                      AND resolvido_em <= limite_resolucao)            AS resolvidos_no_prazo,
                  avg(avaliacao_nota)::numeric(10,2)                   AS csat_media,
                  count(avaliacao_nota)                                AS csat_respostas,
                  avg(EXTRACT(EPOCH FROM (resolvido_em - created_at)))
                    FILTER (WHERE resolvido_em IS NOT NULL)            AS tma_seg
                FROM chamados
                """
            )
        d = dict(row)
        resolvidos = d["resolvidos"] or 0
        d["conformidade_sla"] = (
            round(100.0 * (d["resolvidos_no_prazo"] or 0) / resolvidos, 1) if resolvidos else None
        )
        d["tma_horas"] = round((d["tma_seg"] or 0) / 3600, 1) if d["tma_seg"] else None
        return d

    async def por_status(self, claims: dict) -> dict[str, int]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch("SELECT status, count(*) n FROM chamados GROUP BY status")
        por = {r["status"]: r["n"] for r in rows}
        return {s: por.get(s, 0) for s in ("NOVO", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")}

    async def csat_distribuicao(self, claims: dict) -> dict[int, int]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT avaliacao_nota n, count(*) c FROM chamados WHERE avaliacao_nota IS NOT NULL GROUP BY 1"
            )
        por = {int(r["n"]): r["c"] for r in rows}
        return {i: por.get(i, 0) for i in range(1, 6)}

    async def produtividade(self, claims: dict) -> list[dict[str, Any]]:
        """Chamados resolvidos por operador (produtividade)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(op.nome, 'Sem operador') AS operador,
                       count(*) FILTER (WHERE c.status = 'RESOLVIDO') AS resolvidos,
                       count(*) AS atribuidos
                  FROM chamados c LEFT JOIN perfis op ON op.id = c.operador_id
                 GROUP BY op.nome ORDER BY resolvidos DESC NULLS LAST LIMIT 15
                """
            )
            return [dict(r) for r in rows]

    async def por_departamento(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(d.nome, '—') AS departamento, count(*) AS total
                  FROM chamados c LEFT JOIN departamentos d ON d.id = c.departamento_id
                 GROUP BY d.nome ORDER BY total DESC
                """
            )
            return [dict(r) for r in rows]

    # ---- Gestão de catálogos -------------------------------------------
    async def departamentos(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT id, nome, ativo FROM departamentos ORDER BY nome"
            )
            return [dict(r) for r in rows]

    async def criar_departamento(self, claims: dict, nome: str) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "INSERT INTO departamentos (nome) VALUES ($1) ON CONFLICT (nome) DO NOTHING", nome
            )

    async def toggle_departamento(self, claims: dict, dep_id: str) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE departamentos SET ativo = NOT ativo WHERE id = $1::uuid", dep_id
            )

    async def categorias(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT id, nome, descricao, ativo FROM categorias ORDER BY nome"
            )
            return [dict(r) for r in rows]

    async def criar_categoria(self, claims: dict, nome: str, descricao: str | None) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "INSERT INTO categorias (nome, descricao) VALUES ($1, $2)", nome, descricao
            )

    async def toggle_categoria(self, claims: dict, cat_id: str) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE categorias SET ativo = NOT ativo WHERE id = $1::uuid", cat_id
            )

    async def planos(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT nome, resposta_alta_min, resolucao_alta_min,
                       resposta_default_min, resolucao_default_min, ativo
                  FROM planos_sla ORDER BY nome
                """
            )
            return [dict(r) for r in rows]

    # ---- Export CSV -----------------------------------------------------
    async def exportar(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.codigo, c.titulo, c.status, c.prioridade,
                       dep.nome AS departamento, cat.nome AS categoria,
                       autor.nome AS solicitante, op.nome AS operador,
                       c.created_at, c.limite_resolucao, c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota
                  FROM chamados c
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                  LEFT JOIN perfis op ON op.id = c.operador_id
                 ORDER BY c.created_at DESC
                """
            )
            return [dict(r) for r in rows]


_admin_repo = AdminRepo()


def get_admin_repo() -> AdminRepo:
    return _admin_repo

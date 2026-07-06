"""Repositório do Painel Admin (Fase 5) — KPIs, gestão de catálogos e export.

Acesso restrito ao departamento **TI** (acesso total). As consultas rodam sob
RLS com os claims do usuário; como o admin é TI (`auth_is_ti()` = true), a RLS
devolve a visão completa. Gestão de departamentos/categorias/planos é permitida
pelas policies `*_admin_all` (também `auth_is_ti()`).
"""

from __future__ import annotations

from typing import Any

from app.db import admin_connection, rls_connection


class AdminRepo:
    def _kpi_scope(self, claims: dict, todos_setores: bool):
        """Conexão para os indicadores.

        - ``todos_setores=False`` (Admin de setor): usa a conexão RLS com os claims
          — a RLS já escopa os chamados ao setor do gestor (migration 0020).
        - ``todos_setores=True`` (**TI**): usa ``admin_connection`` (sem RLS) para
          agregar TODOS os setores nos relatórios. Uso controlado e somente-leitura,
          liberado apenas quando a rota confirmou ``is_ti`` (ver admin_context). O
          filtro explícito de ``departamento_id`` continua permitindo focar um setor.
        """
        return admin_connection() if todos_setores else rls_connection(claims)

    async def is_ti(self, claims: dict) -> bool:
        async with rls_connection(claims) as conn:
            return bool(await conn.fetchval("SELECT auth_is_ti()"))

    # ---- KPIs -----------------------------------------------------------
    async def kpis(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False
    ) -> dict[str, Any]:
        async with self._kpi_scope(claims, todos_setores) as conn:
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
                WHERE ($1::uuid IS NULL OR departamento_id = $1::uuid)
                """,
                departamento_id,
            )
        d = dict(row)
        resolvidos = d["resolvidos"] or 0
        d["conformidade_sla"] = (
            round(100.0 * (d["resolvidos_no_prazo"] or 0) / resolvidos, 1) if resolvidos else None
        )
        d["tma_horas"] = round((d["tma_seg"] or 0) / 3600, 1) if d["tma_seg"] else None
        return d

    async def por_status(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False
    ) -> dict[str, int]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """SELECT status, count(*) n FROM chamados
                    WHERE ($1::uuid IS NULL OR departamento_id = $1::uuid)
                    GROUP BY status""",
                departamento_id,
            )
        por = {r["status"]: r["n"] for r in rows}
        return {s: por.get(s, 0) for s in ("NOVO", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")}

    async def csat_distribuicao(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False
    ) -> dict[int, int]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """SELECT avaliacao_nota n, count(*) c FROM chamados
                    WHERE avaliacao_nota IS NOT NULL
                      AND ($1::uuid IS NULL OR departamento_id = $1::uuid)
                    GROUP BY 1""",
                departamento_id,
            )
        por = {int(r["n"]): r["c"] for r in rows}
        return {i: por.get(i, 0) for i in range(1, 6)}

    async def produtividade(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False
    ) -> list[dict[str, Any]]:
        """Chamados resolvidos por operador (produtividade)."""
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(op.nome, 'Sem operador') AS operador,
                       count(*) FILTER (WHERE c.status = 'RESOLVIDO') AS resolvidos,
                       count(*) AS atribuidos
                  FROM chamados c LEFT JOIN perfis op ON op.id = c.operador_id
                 WHERE ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                 GROUP BY op.nome ORDER BY resolvidos DESC NULLS LAST LIMIT 15
                """,
                departamento_id,
            )
            return [dict(r) for r in rows]

    async def avaliacoes_recentes(
        self, claims: dict, *, limite: int = 8, departamento_id: str | None = None,
        todos_setores: bool = False,
    ) -> list[dict[str, Any]]:
        """Últimas avaliações (CSAT) com comentário do solicitante, para o TI ver
        o feedback qualitativo — não só a média. Nota sempre; comentário opcional."""
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT c.codigo, c.titulo, c.avaliacao_nota AS nota,
                       c.avaliacao_comentario AS comentario, c.avaliacao_em AS em,
                       autor.nome AS solicitante
                  FROM chamados c
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                 WHERE c.avaliacao_nota IS NOT NULL
                   AND ($2::uuid IS NULL OR c.departamento_id = $2::uuid)
                 ORDER BY c.avaliacao_em DESC NULLS LAST
                 LIMIT $1
                """,
                limite,
                departamento_id,
            )
            return [dict(r) for r in rows]

    async def por_departamento(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False
    ) -> list[dict[str, Any]]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(d.nome, '—') AS departamento, count(*) AS total
                  FROM chamados c LEFT JOIN departamentos d ON d.id = c.departamento_id
                 WHERE ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                 GROUP BY d.nome ORDER BY total DESC
                """,
                departamento_id,
            )
            return [dict(r) for r in rows]

    async def por_setor(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False
    ) -> list[dict[str, Any]]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(setor, 'Não informado') AS setor, count(*) AS total
                  FROM chamados
                 WHERE ($1::uuid IS NULL OR departamento_id = $1::uuid)
                 GROUP BY setor ORDER BY total DESC
                """,
                departamento_id,
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
                """SELECT c.id, c.nome, c.descricao, c.ativo,
                          c.departamento_id, d.nome AS departamento
                     FROM categorias c
                     LEFT JOIN departamentos d ON d.id = c.departamento_id
                    ORDER BY d.nome NULLS FIRST, c.nome"""
            )
            return [dict(r) for r in rows]

    async def criar_categoria(
        self, claims: dict, nome: str, descricao: str | None, departamento_id: str | None = None
    ) -> None:
        """Cria categoria já vinculada a um departamento (categorias por setor,
        migration 0019). ``departamento_id`` None deixa a categoria sem setor."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                "INSERT INTO categorias (nome, descricao, departamento_id) "
                "VALUES ($1, $2, $3::uuid)",
                nome,
                descricao,
                departamento_id,
            )

    async def toggle_categoria(self, claims: dict, cat_id: str) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE categorias SET ativo = NOT ativo WHERE id = $1::uuid", cat_id
            )

    async def subcategorias(self, claims: dict) -> list[dict[str, Any]]:
        """Subcategorias com o nome da categoria-mãe (gestão do TI)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT s.id, s.nome, s.ativo, s.categoria_id, c.nome AS categoria
                     FROM subcategorias s
                     JOIN categorias c ON c.id = s.categoria_id
                    ORDER BY c.nome, s.nome"""
            )
            return [dict(r) for r in rows]

    async def criar_subcategoria(self, claims: dict, categoria_id: str, nome: str) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "INSERT INTO subcategorias (categoria_id, nome) VALUES ($1::uuid, $2)",
                categoria_id,
                nome,
            )

    async def toggle_subcategoria(self, claims: dict, sub_id: str) -> str | None:
        """Ativa/desativa uma subcategoria; devolve o ``categoria_id`` (para o
        chamador invalidar o cache por-categoria)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                "UPDATE subcategorias SET ativo = NOT ativo WHERE id = $1::uuid RETURNING categoria_id",
                sub_id,
            )
            return str(row["categoria_id"]) if row else None

    async def planos(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT id, nome,
                       resposta_baixa_min, resposta_media_min, resposta_alta_min,
                       resolucao_baixa_min, resolucao_media_min, resolucao_alta_min,
                       resposta_default_min, resolucao_default_min, ativo
                  FROM planos_sla ORDER BY nome
                """
            )
            return [dict(r) for r in rows]

    async def atualizar_plano(
        self, claims: dict, plano_id: str, *, campos: dict[str, int | None]
    ) -> None:
        """Atualiza os tempos de SLA (minutos) de um plano. As colunas são de uma
        allow-list fixa (não vêm do usuário como identificador), então o nome é
        seguro; os valores são parametrizados. URGENTE não é editável (derivado =
        50% de ALTA). Vale para os chamados criados **a partir de agora** (trigger)."""
        cols = [
            "resposta_baixa_min", "resposta_media_min", "resposta_alta_min",
            "resolucao_baixa_min", "resolucao_media_min", "resolucao_alta_min",
            "resposta_default_min", "resolucao_default_min",
        ]
        set_sql = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
        valores = [campos.get(c) for c in cols]
        async with rls_connection(claims) as conn:
            await conn.execute(
                f"UPDATE planos_sla SET {set_sql}, updated_at = now() WHERE id = $1::uuid",
                plano_id,
                *valores,
            )

    # ---- Usuários (gestão de contas — só TI) ----------------------------
    async def usuarios(self, claims: dict) -> list[dict[str, Any]]:
        """Lista os perfis (nome, papel, setor) para a gestão de contas do TI.

        O e-mail vive em ``auth.users`` (fora do alcance do papel ``authenticated``)
        e é resolvido na rota via Admin API; aqui devolvemos o que a RLS permite."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT p.id, p.nome, p.role, p.ativo,
                       d.nome AS departamento, p.departamento_id
                  FROM perfis p
                  LEFT JOIN departamentos d ON d.id = p.departamento_id
                 ORDER BY (p.role = 'CLIENTE'), d.nome NULLS LAST, p.nome
                """
            )
            return [dict(r) for r in rows]

    async def atualizar_papel(
        self, claims: dict, user_id: str, *, role: str, departamento_id: str | None
    ) -> None:
        """Grava papel + setor na tabela ``perfis`` (RLS: só o TI pode — policy
        ``perfis_admin_all``/``auth_is_ti()``). O ``app_metadata.role`` do JWT é
        atualizado à parte, via Admin API, pela rota (dual-write da Seção 3.2)."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                """UPDATE perfis
                      SET role = $2::papel_usuario,
                          departamento_id = $3::uuid
                    WHERE id = $1::uuid""",
                user_id,
                role,
                departamento_id,
            )

    # ---- Export CSV -----------------------------------------------------
    async def exportar(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.codigo, c.titulo, c.status, c.prioridade,
                       dep.nome AS departamento, cat.nome AS categoria,
                       autor.nome AS solicitante, op.nome AS operador,
                       c.created_at, c.limite_resolucao, c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota, c.avaliacao_em, c.avaliacao_comentario
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

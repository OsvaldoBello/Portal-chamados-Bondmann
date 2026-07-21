"""Repositório do Painel Admin (Fase 5) — KPIs, gestão de catálogos e export.

Acesso restrito ao departamento **TI** (acesso total). As consultas rodam sob
RLS com os claims do usuário; como o admin é TI (`auth_is_ti()` = true), a RLS
devolve a visão completa. Gestão de departamentos/categorias/planos é permitida
pelas policies `*_admin_all` (também `auth_is_ti()`).
"""

from __future__ import annotations

from datetime import datetime
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
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
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
                  AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
                  AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz)
                """,
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
        d = dict(row)
        resolvidos = d["resolvidos"] or 0
        d["conformidade_sla"] = (
            round(100.0 * (d["resolvidos_no_prazo"] or 0) / resolvidos, 1) if resolvidos else None
        )
        d["tma_horas"] = round((d["tma_seg"] or 0) / 3600, 1) if d["tma_seg"] else None
        return d

    async def por_status(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
    ) -> dict[str, int]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """SELECT status, count(*) n FROM chamados
                    WHERE ($1::uuid IS NULL OR departamento_id = $1::uuid)
                      AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz)
                    GROUP BY status""",
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
        por = {r["status"]: r["n"] for r in rows}
        return {s: por.get(s, 0) for s in ("NOVO", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")}

    async def csat_distribuicao(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
    ) -> dict[int, int]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """SELECT avaliacao_nota n, count(*) c FROM chamados
                    WHERE avaliacao_nota IS NOT NULL
                      AND ($1::uuid IS NULL OR departamento_id = $1::uuid)
                      AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz)
                    GROUP BY 1""",
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
        por = {int(r["n"]): r["c"] for r in rows}
        return {i: por.get(i, 0) for i in range(1, 6)}

    async def produtividade(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
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
                   AND ($2::timestamptz IS NULL OR c.created_at >= $2::timestamptz)
                   AND ($3::timestamptz IS NULL OR c.created_at < $3::timestamptz)
                 GROUP BY op.nome ORDER BY resolvidos DESC NULLS LAST LIMIT 15
                """,
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
            return [dict(r) for r in rows]

    async def avaliacoes_recentes(
        self, claims: dict, *, limite: int = 8, departamento_id: str | None = None,
        todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Últimas avaliações (CSAT) com comentário do solicitante, para o TI ver
        o feedback qualitativo — não só a média. Nota sempre; comentário opcional.
        Inclui ``id`` do chamado para linkar direto ao detalhe (Workspace)."""
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.avaliacao_nota AS nota,
                       c.avaliacao_comentario AS comentario, c.avaliacao_em AS em,
                       autor.nome AS solicitante
                  FROM chamados c
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                 WHERE c.avaliacao_nota IS NOT NULL
                   AND ($2::uuid IS NULL OR c.departamento_id = $2::uuid)
                   AND ($3::timestamptz IS NULL OR c.created_at >= $3::timestamptz)
                   AND ($4::timestamptz IS NULL OR c.created_at < $4::timestamptz)
                 ORDER BY c.avaliacao_em DESC NULLS LAST
                 LIMIT $1
                """,
                limite,
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
            return [dict(r) for r in rows]

    async def chamados_por_nota(
        self, claims: dict, *, nota: int, departamento_id: str | None = None,
        todos_setores: bool = False, busca: str | None = None,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
        limite: int = 300,
    ) -> list[dict[str, Any]]:
        """Chamados com uma nota de CSAT específica (1-5) — alimenta o modal que
        abre ao clicar numa barra do gráfico "Distribuição do CSAT"."""
        busca_norm = f"%{busca.strip()}%" if busca and busca.strip() else None
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status, c.avaliacao_nota AS nota,
                       c.avaliacao_comentario AS comentario, c.avaliacao_em AS em,
                       autor.nome AS solicitante
                  FROM chamados c
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                 WHERE c.avaliacao_nota = $1
                   AND ($2::uuid IS NULL OR c.departamento_id = $2::uuid)
                   AND ($3::timestamptz IS NULL OR c.created_at >= $3::timestamptz)
                   AND ($4::timestamptz IS NULL OR c.created_at < $4::timestamptz)
                   AND ($5::text IS NULL OR c.codigo ILIKE $5 OR c.titulo ILIKE $5
                        OR autor.nome ILIKE $5)
                 ORDER BY c.avaliacao_em DESC NULLS LAST
                 LIMIT $6
                """,
                nota,
                departamento_id,
                periodo_inicio,
                periodo_fim,
                busca_norm,
                limite,
            )
            return [dict(r) for r in rows]

    async def por_departamento(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
    ) -> list[dict[str, Any]]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(d.nome, '—') AS departamento, count(*) AS total
                  FROM chamados c LEFT JOIN departamentos d ON d.id = c.departamento_id
                 WHERE ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                   AND ($2::timestamptz IS NULL OR c.created_at >= $2::timestamptz)
                   AND ($3::timestamptz IS NULL OR c.created_at < $3::timestamptz)
                 GROUP BY d.nome ORDER BY total DESC
                """,
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
            return [dict(r) for r in rows]

    async def por_setor(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
    ) -> list[dict[str, Any]]:
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(setor, 'Não informado') AS setor, count(*) AS total
                  FROM chamados
                 WHERE ($1::uuid IS NULL OR departamento_id = $1::uuid)
                   AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
                   AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz)
                 GROUP BY setor ORDER BY total DESC
                """,
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
            return [dict(r) for r in rows]


    # ---- Gestão de catálogos -------------------------------------------
    async def departamentos(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT id, nome, ativo, recebe_chamados FROM departamentos ORDER BY nome"
            )
            return [dict(r) for r in rows]

    async def criar_departamento(
        self, claims: dict, nome: str, *, recebe_chamados: bool = False
    ) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                """INSERT INTO departamentos (nome, recebe_chamados) VALUES ($1, $2)
                   ON CONFLICT (nome) DO NOTHING""",
                nome, recebe_chamados,
            )

    async def toggle_departamento(self, claims: dict, dep_id: str) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE departamentos SET ativo = NOT ativo WHERE id = $1::uuid", dep_id
            )

    async def toggle_recebe_departamento(self, claims: dict, dep_id: str) -> None:
        """Liga/desliga se o setor tem fila de atendimento (pode ser destino de
        chamado). Guarda-corpo contra staff apontar para setor sem fila é o trigger
        ``enforce_departamento_recebe_chamados`` (0027)."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE departamentos SET recebe_chamados = NOT recebe_chamados WHERE id = $1::uuid",
                dep_id,
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

    async def obter_papel(self, claims: dict, user_id: str) -> str | None:
        """Relê ``perfis.role`` após uma promoção (Sprint 1 / item 1.5, M12) —
        confirma que a escrita em :meth:`atualizar_papel` de fato aplicou
        (nunca assume; a UPDATE não checa rowcount)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow("SELECT role FROM perfis WHERE id = $1::uuid", user_id)
            return row["role"] if row else None

    async def atualizar_avatar(self, claims: dict, user_id: str, *, avatar_path: str) -> None:
        """Grava o ``avatar_path`` de OUTRO usuário — só o TI pode (o trigger
        ``enforce_perfil_self_so_avatar``, migration 0033, libera qualquer coluna
        para quem é TI; para os demais, só a própria linha). Usado na criação de
        conta pela tela de admin (Fase 7), quando o TI já envia a foto junto."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE perfis SET avatar_path = $2 WHERE id = $1::uuid",
                user_id,
                avatar_path,
            )

    _MESES_MAP = {1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN",
                  7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ"}

    @classmethod
    def _mes_label(cls, d) -> str:
        """"JAN/26" a partir de um `date`/`datetime` (dia 1 do mês)."""
        return f"{cls._MESES_MAP[d.month]}/{d.year % 100:02d}"

    async def mkt_dashboard_data(self, claims: dict) -> dict[str, Any]:
        """Série histórica e estatísticas do Dashboard de Marketing — 🔁 Fase 6
        (2026-07-09): o banco guarda só o dado bruto (`chamados`,
        `marketing_midia_regional`); as agregações mensais vêm de VIEWS
        (`vw_marketing_volume_mensal`/`vw_marketing_setor_mensal`,
        `supabase/migrations/0032_marketing_dinamico_views.sql`) em vez de puxar
        TODOS os chamados do Marketing já abertos para agregar em Python — só a
        lista de atrasos (que precisa de título/causa por chamado) é buscada
        linha a linha, e só as linhas realmente atrasadas."""
        async with rls_connection(claims) as conn:
            volume_rows = await conn.fetch(
                """SELECT mes, total, concluidas, abertas, em_andamento, volume,
                          mkt_orig, sol_orig, atrasos, tempo_medio
                     FROM vw_marketing_volume_mensal ORDER BY mes"""
            )
            setor_rows = await conn.fetch(
                "SELECT mes, setor, total FROM vw_marketing_setor_mensal ORDER BY mes"
            )
            atraso_rows = await conn.fetch(
                """
                SELECT c.titulo,
                       date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
                       EXTRACT(EPOCH FROM (COALESCE(c.resolvido_em, now()) - c.created_at)) / 86400.0 AS dias,
                       c.causa_atraso
                  FROM chamados c
                  JOIN departamentos d ON d.id = c.departamento_id
                 WHERE d.nome = 'Marketing'
                   AND (COALESCE(c.resolvido_em, now()) - c.created_at) > interval '5 days'
                 ORDER BY c.created_at ASC
                """
            )
            midia_rows = await conn.fetch(
                """SELECT mes, investimento, regioes, descontinuidades, aderencias
                     FROM marketing_midia_regional ORDER BY mes ASC"""
            )

        volume_por_mes = {r["mes"]: dict(r) for r in volume_rows}
        midia_list = [dict(r) for r in midia_rows]

        # Todo mês com chamado OU com registro de mídia regional entra na série
        # (mesmo sem chamado nenhum — mês futuro cadastrado com antecedência, etc.).
        todos_meses = sorted(set(volume_por_mes) | {m["mes"] for m in midia_list})

        monthly_list = []
        dept_by_month: dict[str, dict[str, int]] = {}
        for mes in todos_meses:
            label = self._mes_label(mes)
            v = volume_por_mes.get(mes)
            total = v["total"] if v else 0
            concluidas = v["concluidas"] if v else 0
            mkt_orig = v["mkt_orig"] if v else 0
            monthly_list.append({
                "label": label,
                "total": total,
                "concluidas": concluidas,
                "em_andamento": v["em_andamento"] if v else 0,
                "abertas": v["abertas"] if v else 0,
                "volume": v["volume"] if v else 0,
                "mkt_orig": mkt_orig,
                "sol_orig": v["sol_orig"] if v else 0,
                "tempo_medio": float(v["tempo_medio"]) if v and v["tempo_medio"] is not None else 0.0,
                "atrasos": v["atrasos"] if v else 0,
                "pct_conc": round(100.0 * concluidas / total, 1) if total else 0.0,
                "pct_mkt": round(100.0 * mkt_orig / total, 1) if total else 0.0,
            })
            dept_by_month[label] = {
                r["setor"]: r["total"] for r in setor_rows if r["mes"] == mes
            }

        atrasos_data = [
            {
                "nome": r["titulo"] or "Sem assunto",
                "mes": self._mes_label(r["mes"]),
                "dias": int(round(max(0.0, r["dias"]))),
                "causa": r["causa_atraso"] or "Sem causa registrada",
            }
            for r in atraso_rows
        ]

        midia_final = {
            "meses": [self._MESES_MAP[m["mes"].month].capitalize() for m in midia_list],
            "investimento": [float(m["investimento"]) for m in midia_list],
            "regioes": [int(m["regioes"]) for m in midia_list],
            "descontinuidades": [int(m["descontinuidades"]) for m in midia_list],
            "aderencias": [int(m["aderencias"]) for m in midia_list],
        }

        return {
            "monthly": monthly_list,
            "deptByMonth": dept_by_month,
            "atrasosData": atrasos_data,
            "midia": midia_final,
        }

    # ---- Mídia Regional do Marketing (CRUD — Fase 6) ---------------------
    async def marketing_midia_regional(self, claims: dict) -> list[dict[str, Any]]:
        """Lista os registros de mídia regional (para o CRUD em `/admin/gestao`)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT id, mes, investimento, regioes, descontinuidades, aderencias
                     FROM marketing_midia_regional ORDER BY mes DESC"""
            )
            return [dict(r) for r in rows]

    async def upsert_marketing_midia_regional(
        self, claims: dict, *, mes, investimento: float, regioes: int,
        descontinuidades: int, aderencias: int,
    ) -> None:
        """Cria ou atualiza o registro de um mês (`mes` = 1º dia do mês, `date`).
        Sem seed hardcoded: qualquer mês novo entra por aqui, não por migration —
        é o que tira o "engessamento" da tabela (Fase 6)."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                """
                INSERT INTO marketing_midia_regional (mes, investimento, regioes, descontinuidades, aderencias)
                VALUES ($1::date, $2, $3, $4, $5)
                ON CONFLICT (mes) DO UPDATE
                   SET investimento = EXCLUDED.investimento,
                       regioes = EXCLUDED.regioes,
                       descontinuidades = EXCLUDED.descontinuidades,
                       aderencias = EXCLUDED.aderencias
                """,
                mes, investimento, regioes, descontinuidades, aderencias,
            )

    # ---- Feriados dinâmicos via biblioteca `holidays` (Fase 5) -----------
    async def sincronizar_feriados(self, claims: dict, feriados: list[tuple]) -> int:
        """Upsert dos feriados nacionais (`app/domain/feriados.py`). Nunca
        sobrescreve o que já existe (`ON CONFLICT DO NOTHING`) — preserva
        feriados locais/pontos facultativos cadastrados manualmente. Devolve
        quantos foram efetivamente inseridos (novos)."""
        if not feriados:
            return 0
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                INSERT INTO feriados (data, descricao)
                SELECT * FROM unnest($1::date[], $2::text[])
                ON CONFLICT (data) DO NOTHING
                RETURNING data
                """,
                [d for d, _ in feriados],
                [n for _, n in feriados],
            )
            return len(rows)

    # ---- Prioridade dinâmica do Marketing (Fase 4) -----------------------
    async def recalcular_prioridade_marketing(self, claims: dict) -> int:
        """Roda sob demanda o recálculo de prioridade do Marketing (função SQL
        `recalcular_prioridade_marketing`, migration 0031). O caminho automático
        diário é o `pg_cron`, quando disponível no projeto — esta chamada existe
        para o botão manual do TI e para um scheduler externo de fallback.
        `admin_connection` porque a função atualiza chamados de QUALQUER autor do
        Marketing, fora do escopo de RLS de um único usuário."""
        async with admin_connection() as conn:
            return await conn.fetchval("SELECT recalcular_prioridade_marketing()")

    # ---- Export CSV -----------------------------------------------------
    async def exportar(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.codigo, c.titulo, c.descricao, c.status, c.prioridade,
                       dep.nome AS departamento, cat.nome AS categoria, sub.nome AS subcategoria,
                       autor.nome AS solicitante, op.nome AS operador,
                       c.created_at, c.limite_resolucao, c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota, c.avaliacao_em, c.avaliacao_comentario
                  FROM chamados c
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN subcategorias sub ON sub.id = c.subcategoria_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                  LEFT JOIN perfis op ON op.id = c.operador_id
                 ORDER BY c.created_at DESC
                """
            )
            return [dict(r) for r in rows]


_admin_repo = AdminRepo()


def get_admin_repo() -> AdminRepo:
    return _admin_repo

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
    """Indicadores e gestão do painel Admin.

    🔁 **Combinação de chamados (migration 0065):** toda agregação desta classe
    ignora duplicados (``chamados.chamado_principal_id IS NOT NULL``) — é o
    ponto da feature. Um incidente que gerou 8 chamados iguais passa a contar
    como 1 no volume, no CSAT, no TMA, na conformidade de SLA e na
    produtividade por operador. O ÚNICO lugar que continua listando o duplicado
    é o export CSV (:meth:`exportar`), com a coluna "Combinado com": relatório
    é dado bruto, e quem analisa filtra a coluna se quiser — apagar a linha
    esconderia que o incidente teve 8 pessoas afetadas.
    """

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
        """KPIs do mês selecionado.

        **Ancoragem por métrica, não um único corte por `created_at` (2026-07-22):**
        um chamado aberto em maio e fechado em junho deve contar para JUNHO nos
        indicadores de fechamento — ``total``/``abertos`` (volume de entrada) usam
        ``created_at``, mas ``resolvidos``/``resolvidos_no_prazo``/``tma`` usam
        ``resolvido_em`` — e o CSAT também (ver abaixo). Antes, um único
        ``WHERE created_at BETWEEN ...`` filtrava a linha inteira pela data de
        ABERTURA, então um chamado fechado no mês corrente mas aberto num mês
        anterior simplesmente desaparecia dos resolvidos/CSAT/TMA do mês —
        números de "resolvidos este mês" ficavam sistematicamente baixos.

        **CSAT ancorado em `resolvido_em`, não em `avaliacao_em` (2026-08-03,
        pedido do usuário):** o CSAT do mês é a nota do ATENDIMENTO entregue
        naquele mês, então a base é o conjunto de chamados **resolvidos no mês**
        (os que têm nota entram na média; os sem nota simplesmente não entram,
        porque ``avg``/``count`` de uma coluna ignoram NULL). Com a âncora
        anterior (``avaliacao_em``), a nota migrava para o mês em que o autor
        clicou nas estrelas: um chamado resolvido em 30/jun e avaliado em 02/jul
        puxava o CSAT de JULHO, e o CSAT de junho ficava incompleto e ainda
        mudava dias depois do mês fechado. ``csat_respostas`` passa a significar
        "quantos dos resolvidos do mês foram avaliados" — comparável direto com
        ``resolvidos`` (taxa de resposta), o que antes não era possível.

        **TMA de "Projetos" separado do TMA geral (2026-07-24, pedido do usuário):**
        chamados finalizados a partir da coluna "Projetos" do Kanban do TI
        (migration `0057`) são trabalho mais robusto — misturar o tempo de
        resolução deles no TMA geral distorceria pra cima a métrica de
        velocidade do time nos chamados normais. Não existe uma coluna
        ``chamados.eh_projeto`` (o status muda pra `RESOLVIDO` na conclusão,
        perdendo o valor `PROJETOS`) — a identificação usa `historico_chamados`
        (`STATUS_ALTERADO`, `detalhes = {"de": ..., "para": ...}`, gravado em
        `AtendimentoRepo.alterar_status`): um chamado só conta como "Projeto"
        se a transição mais recente que o levou a `RESOLVIDO` partiu de
        `PROJETOS` (subquery correlacionada `resolvido_de`, usa o índice
        `idx_historico_chamado (chamado_id, created_at)`). ``resolvidos``/
        ``resolvidos_no_prazo``/``conformidade_sla`` continuam somando Projetos
        junto dos demais (o usuário só pediu separação no TMA); só ``tma_seg``
        passa a EXCLUIR Projetos, com ``tma_projetos_seg``/``projetos_resolvidos``
        como métricas próprias.
        """
        async with self._kpi_scope(claims, todos_setores) as conn:
            row = await conn.fetchrow(
                """
                WITH base AS (
                  SELECT c.status, c.created_at, c.resolvido_em, c.limite_resolucao,
                         c.avaliacao_nota, c.avaliacao_em,
                         (SELECT h.detalhes->>'de'
                            FROM historico_chamados h
                           WHERE h.chamado_id = c.id
                             AND h.acao = 'STATUS_ALTERADO'
                             AND h.detalhes->>'para' = 'RESOLVIDO'
                           ORDER BY h.created_at DESC
                           LIMIT 1)                                   AS resolvido_de
                    FROM chamados c
                   WHERE ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                     AND c.chamado_principal_id IS NULL
                )
                SELECT
                  count(*) FILTER (
                    WHERE ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz))
                                                                        AS total,
                  count(*) FILTER (
                    WHERE status <> 'RESOLVIDO'
                      AND ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz))
                                                                        AS abertos,
                  count(*) FILTER (
                    WHERE resolvido_em IS NOT NULL
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz))
                                                                        AS resolvidos,
                  count(*) FILTER (
                    WHERE resolvido_em IS NOT NULL AND limite_resolucao IS NOT NULL
                      AND resolvido_em <= limite_resolucao
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz))
                                                                        AS resolvidos_no_prazo,
                  avg(avaliacao_nota) FILTER (
                    WHERE resolvido_em IS NOT NULL
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz))::numeric(10,2)
                                                                        AS csat_media,
                  count(avaliacao_nota) FILTER (
                    WHERE resolvido_em IS NOT NULL
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz))
                                                                        AS csat_respostas,
                  avg(EXTRACT(EPOCH FROM (resolvido_em - created_at))) FILTER (
                    WHERE resolvido_em IS NOT NULL
                      AND resolvido_de IS DISTINCT FROM 'PROJETOS'
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz))
                                                                        AS tma_seg,
                  count(*) FILTER (
                    WHERE resolvido_em IS NOT NULL AND resolvido_de = 'PROJETOS'
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz))
                                                                        AS projetos_resolvidos,
                  avg(EXTRACT(EPOCH FROM (resolvido_em - created_at))) FILTER (
                    WHERE resolvido_em IS NOT NULL AND resolvido_de = 'PROJETOS'
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz))
                                                                        AS tma_projetos_seg
                FROM base
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
        d["tma_projetos_horas"] = (
            round((d["tma_projetos_seg"] or 0) / 3600, 1) if d["tma_projetos_seg"] else None
        )
        return d

    async def por_status(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
    ) -> dict[str, int]:
        """Distribuição por status no mês. RESOLVIDO é ancorado em ``resolvido_em``
        (fechado no mês conta pro mês, mesmo aberto antes — mesma correção da
        Seção `kpis`); os demais status (ainda abertos) usam ``created_at``."""
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """SELECT status, count(*) n FROM chamados
                    WHERE ($1::uuid IS NULL OR departamento_id = $1::uuid)
                      AND chamado_principal_id IS NULL
                      AND (
                        CASE WHEN status = 'RESOLVIDO' THEN
                          ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                          AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz)
                        ELSE
                          ($2::timestamptz IS NULL OR created_at >= $2::timestamptz)
                          AND ($3::timestamptz IS NULL OR created_at < $3::timestamptz)
                        END
                      )
                    GROUP BY status""",
                departamento_id,
                periodo_inicio,
                periodo_fim,
            )
        por = {r["status"]: r["n"] for r in rows}
        return {s: por.get(s, 0) for s in
                ("NOVO", "PROJETOS", "EM_ATENDIMENTO", "RESPOSTA_CLIENTE", "AGUARDANDO", "RESOLVIDO")}

    async def csat_distribuicao(
        self, claims: dict, *, departamento_id: str | None = None, todos_setores: bool = False,
        periodo_inicio: datetime | None = None, periodo_fim: datetime | None = None,
    ) -> dict[int, int]:
        """Distribuição de notas CSAT do mês, ancorada em ``resolvido_em`` — as
        barras somam exatamente as ``csat_respostas`` do KPI "CSAT médio"
        (:meth:`kpis`), que desde 2026-08-03 é a nota dos chamados RESOLVIDOS no
        mês. Ancorar aqui em ``avaliacao_em`` faria o gráfico contar um conjunto
        diferente do card logo acima dele."""
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """SELECT avaliacao_nota n, count(*) c FROM chamados
                    WHERE avaliacao_nota IS NOT NULL
                      AND chamado_principal_id IS NULL
                      AND ($1::uuid IS NULL OR departamento_id = $1::uuid)
                      AND resolvido_em IS NOT NULL
                      AND ($2::timestamptz IS NULL OR resolvido_em >= $2::timestamptz)
                      AND ($3::timestamptz IS NULL OR resolvido_em < $3::timestamptz)
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
        """Chamados resolvidos por operador (produtividade) no mês.

        ``resolvidos`` é ancorado em ``resolvido_em`` (fechado no mês conta pro
        mês, mesmo aberto antes); ``atribuidos`` (volume atribuído ao operador,
        independente do status) continua ancorado em ``created_at``."""
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT COALESCE(op.nome, 'Sem operador') AS operador,
                       count(*) FILTER (
                         WHERE c.status = 'RESOLVIDO'
                           AND ($2::timestamptz IS NULL OR c.resolvido_em >= $2::timestamptz)
                           AND ($3::timestamptz IS NULL OR c.resolvido_em < $3::timestamptz))
                                                                        AS resolvidos,
                       count(*) FILTER (
                         WHERE ($2::timestamptz IS NULL OR c.created_at >= $2::timestamptz)
                           AND ($3::timestamptz IS NULL OR c.created_at < $3::timestamptz))
                                                                        AS atribuidos
                  FROM chamados c LEFT JOIN perfis op ON op.id = c.operador_id
                 WHERE ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                   AND c.chamado_principal_id IS NULL
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
        Inclui ``id`` do chamado para linkar direto ao detalhe (Workspace).

        Período ancorado em ``resolvido_em`` (mesmo critério do KPI e do gráfico
        desde 2026-08-03): a lista é o feedback qualitativo POR TRÁS da média do
        mês, então precisa listar os mesmos chamados que a média considera — uma
        nota dada em julho sobre um chamado resolvido em junho é feedback do
        atendimento de junho. A ordenação continua por ``avaliacao_em`` (mais
        recentes primeiro), que é o que faz dela uma lista de "últimas"."""
        async with self._kpi_scope(claims, todos_setores) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.avaliacao_nota AS nota,
                       c.avaliacao_comentario AS comentario, c.avaliacao_em AS em,
                       autor.nome AS solicitante
                  FROM chamados c
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                 WHERE c.avaliacao_nota IS NOT NULL
                   AND c.chamado_principal_id IS NULL
                   AND ($2::uuid IS NULL OR c.departamento_id = $2::uuid)
                   AND c.resolvido_em IS NOT NULL
                   AND ($3::timestamptz IS NULL OR c.resolvido_em >= $3::timestamptz)
                   AND ($4::timestamptz IS NULL OR c.resolvido_em < $4::timestamptz)
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
        abre ao clicar numa barra do gráfico "Distribuição do CSAT".

        Período ancorado em ``resolvido_em`` — mesmo critério de
        :meth:`avaliacoes_recentes`/:meth:`csat_distribuicao`, para o modal bater
        com a barra que o usuário clicou."""
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
                   AND c.chamado_principal_id IS NULL
                   AND ($2::uuid IS NULL OR c.departamento_id = $2::uuid)
                   AND c.resolvido_em IS NOT NULL
                   AND ($3::timestamptz IS NULL OR c.resolvido_em >= $3::timestamptz)
                   AND ($4::timestamptz IS NULL OR c.resolvido_em < $4::timestamptz)
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
                   AND c.chamado_principal_id IS NULL
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
                   AND chamado_principal_id IS NULL
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
                """SELECT c.id, c.nome, c.descricao, c.ativo, c.publico_alvo,
                          c.departamento_id, d.nome AS departamento
                     FROM categorias c
                     LEFT JOIN departamentos d ON d.id = c.departamento_id
                    ORDER BY d.nome NULLS FIRST, c.nome"""
            )
            return [dict(r) for r in rows]

    async def criar_categoria(
        self,
        claims: dict,
        nome: str,
        descricao: str | None,
        departamento_id: str | None = None,
        publico_alvo: str = "AMBOS",
    ) -> None:
        """Cria categoria já vinculada a um departamento (categorias por setor,
        migration 0019). ``departamento_id`` None deixa a categoria sem setor.
        ``publico_alvo`` (0076) restringe quem vê a categoria na abertura:
        CLT, PJ (representantes) ou AMBOS (default — comportamento pré-0076)."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                "INSERT INTO categorias (nome, descricao, departamento_id, publico_alvo) "
                "VALUES ($1, $2, $3::uuid, $4)",
                nome,
                descricao,
                departamento_id,
                publico_alvo,
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
                       resposta_default_min, resolucao_default_min,
                       projeto_dias, ativo
                  FROM planos_sla ORDER BY nome
                """
            )
            return [dict(r) for r in rows]

    async def atualizar_plano(
        self, claims: dict, plano_id: str, *,
        campos: dict[str, int | None], projeto_dias: int | None = None,
    ) -> None:
        """Atualiza os tempos de SLA (minutos) de um plano. As colunas são de uma
        allow-list fixa (não vêm do usuário como identificador), então o nome é
        seguro; os valores são parametrizados. URGENTE não é editável (derivado =
        50% de ALTA). Vale para os chamados criados **a partir de agora** (trigger).

        ``projeto_dias`` (0066) é o prazo PADRÃO da coluna "Projetos", em dias
        corridos — fora da allow-list acima porque a unidade é outra e a coluna é
        ``NOT NULL``: ``None`` (campo em branco/inválido) **mantém** o valor
        atual, em vez de apagá-lo como acontece com os minutos."""
        cols = [
            "resposta_baixa_min", "resposta_media_min", "resposta_alta_min",
            "resolucao_baixa_min", "resolucao_media_min", "resolucao_alta_min",
            "resposta_default_min", "resolucao_default_min",
        ]
        set_sql = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
        valores = [campos.get(c) for c in cols]
        async with rls_connection(claims) as conn:
            await conn.execute(
                f"UPDATE planos_sla SET {set_sql}, "
                f"projeto_dias = COALESCE(${len(cols) + 2}::integer, projeto_dias), "
                "updated_at = now() WHERE id = $1::uuid",
                plano_id,
                *valores,
                projeto_dias,
            )

    # ---- Usuários (criar/promover/excluir conta — só TI; foto — ver abaixo) ----
    async def usuarios(self, claims: dict) -> list[dict[str, Any]]:
        """Lista os perfis (nome, papel, setor, avatar) para a tela de contas.

        O e-mail vive em ``auth.users`` (fora do alcance do papel ``authenticated``)
        e é resolvido na rota via Admin API; aqui devolvemos o que a RLS permite.
        ``avatar_path``/``updated_at`` alimentam a miniatura + cache-busting da
        foto (``app.avatar_storage.avatar_public_url``) na própria listagem.
        Ordem alfabética pelo nome (pedido do usuário, 2026-07-27) — a tela é a
        mesma para TI e para o Operador do Marketing (``pode_editar_avatares``),
        então a troca vale para os dois."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT p.id, p.nome, p.role, p.ativo, p.avatar_path, p.updated_at,
                       d.nome AS departamento, p.departamento_id
                  FROM perfis p
                  LEFT JOIN departamentos d ON d.id = p.departamento_id
                 ORDER BY lower(p.nome)
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
        """Grava o ``avatar_path`` de OUTRO usuário — TI, qualquer ADMIN de setor
        ou o OPERADOR do Marketing podem (policy ``perfis_update_avatar_staff``,
        migration 0052; o TI segue coberto por ``perfis_admin_all``, que também
        libera as demais colunas). Para os papéis não-TI, o trigger
        ``enforce_perfil_self_so_avatar`` (migration 0033) segue vetando
        qualquer coluna além de ``avatar_path``/``updated_at`` — é a única coisa
        que dá pra alterar no perfil de outra pessoa por essa via."""
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
        linha a linha, e só as linhas realmente atrasadas.

        **Baseline histórico** (`marketing_volume_baseline`, migration `0069`,
        pedido do usuário 2026-07-31; **junho/26 acrescentado pela `0073`**,
        2026-08-03): meses anteriores ao uso real do Portal pelo Marketing
        (jan-jun/26 — o time controlava fora do sistema) têm pouquíssimos
        chamados de verdade, o que fazia o gráfico "cair pra zero" nesses meses
        mesmo tendo movimento real. Quando existe uma linha de baseline pro mês,
        ela SUBSTITUI os números calculados da view (a contagem por ticket não
        reflete o histórico pré-sistema); sem baseline, segue vindo 100% da
        view, como sempre. O mesmo vale para a quebra por setor solicitante
        (`marketing_setor_baseline`, migration `0075`, 2026-08-03): jan-jun/26
        têm setor atribuído no controle do time, e a linha de baseline
        substitui a da view.

        **`operadorByMonth`** (pedido do usuário 2026-08-03): chamados atendidos
        por operador do Marketing, por mês, com ``atendidos`` ancorado em
        ``created_at`` e ``resolvidos`` em ``resolvido_em`` (ver o comentário na
        query). Só sai de chamado REAL do Portal — os meses de baseline não têm
        essa informação (o agregado pré-sistema não guarda quem atendeu), então
        vêm vazios de propósito e a aba avisa isso.
        ``resolvidosSemOperadorByMonth`` acompanha, para a soma das barras mais
        os órfãos fechar com as "Concluídas" do mês.

        **Cohort do mês** (migrations `0081`/`0082`, bug reportado 2026-08-11 —
        a barra "Total" de ago/26 dava 16 contra 21 somando as barras
        coloridas). O mês de um chamado é o mês em que ele foi ENTREGUE
        (``resolvido_em``) ou, se ainda está em pé, o mês em que ENTROU
        (``created_at``). Disso saem, todos fechando com o mesmo número::

            total = concluidas + em_andamento + abertas   (aba 1)
                  = mkt_orig + sol_orig                   (aba 3)
                  = soma de ``deptByMonth[mes]``          (aba 4)

        Fora do cohort ficam ``atrasos`` e ``tempo_medio``, ancorados em
        ``created_at`` junto com a lista ``atrasosData`` (que é por mês de
        abertura) — o denominador deles é ``aberturas``, não ``total``."""
        async with rls_connection(claims) as conn:
            volume_rows = await conn.fetch(
                """SELECT mes, total, concluidas, abertas, em_andamento, volume,
                          mkt_orig, sol_orig, atrasos, tempo_medio, aberturas
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
            baseline_rows = await conn.fetch(
                """SELECT mes, total, concluidas, em_andamento, abertas, volume,
                          mkt_orig, sol_orig, tempo_medio, atrasos
                     FROM marketing_volume_baseline ORDER BY mes ASC"""
            )
            setor_baseline_rows = await conn.fetch(
                "SELECT mes, setor, total FROM marketing_setor_baseline ORDER BY mes, setor"
            )
            # Chamados atendidos por operador (pedido do usuário 2026-08-03).
            # `JOIN perfis` (não LEFT): chamado sem operador não vira barra
            # "Sem operador" — a aba é sobre quem atendeu, e no Marketing a
            # maior parte da fila entra sem dono até alguém pegar.
            # `Admin Marketing` fora por nome: é a conta ADMIN genérica do
            # setor, não uma pessoa que atende (mesmo padrão de lookup por
            # nome do perfil de serviço "Assistente IA", ver
            # `supabase/registro_usuarios.sql`) — filtrar por `role` não
            # serviria, porque Felipe também é ADMIN.
            #
            # 🔁 2026-08-03 — **cada métrica no seu próprio mês** (bug real
            # reportado pelo usuário: "não está batendo"). A primeira versão
            # ancorava as DUAS em `created_at` e contava `status='RESOLVIDO'`
            # dentro do mês de abertura. Resultado: a aba dizia "JAN: 2
            # resolvidos" enquanto o dashboard dizia "0 concluídas em janeiro"
            # (os 2 foram abertos em janeiro mas só resolvidos em julho), e
            # dizia 42 resolvidos em julho contra 51 concluídas — os 6 de
            # julho abertos em meses anteriores caíam no mês errado.
            #
            #   atendidos  → `created_at`   (demanda que ENTROU no mês e é dele)
            #   resolvidos → `resolvido_em` (demanda que ELE FECHOU no mês)
            #
            # É a mesma separação de âncora que `kpis`/`produtividade` já fazem
            # (Seção "Ancoragem por métrica"), e é o que faz os resolvidos
            # baterem com as "Concluídas" do mesmo mês. Consequência esperada:
            # num mês os resolvidos podem passar os atendidos (fechou coisa que
            # entrou antes) — os rótulos do gráfico dizem isso.
            operador_rows = await conn.fetch(
                """
                SELECT mes, operador,
                       sum(atendidos)::int  AS atendidos,
                       sum(resolvidos)::int AS resolvidos
                  FROM (
                    SELECT date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
                           op.nome AS operador, count(*) AS atendidos, 0 AS resolvidos
                      FROM chamados c
                      JOIN departamentos d ON d.id = c.departamento_id
                      JOIN perfis op ON op.id = c.operador_id
                     WHERE d.nome = 'Marketing'
                       AND c.chamado_principal_id IS NULL
                       AND op.nome <> 'Admin Marketing'
                     GROUP BY 1, 2
                    UNION ALL
                    SELECT date_trunc('month', c.resolvido_em AT TIME ZONE 'America/Sao_Paulo')::date,
                           op.nome, 0, count(*)
                      FROM chamados c
                      JOIN departamentos d ON d.id = c.departamento_id
                      JOIN perfis op ON op.id = c.operador_id
                     WHERE d.nome = 'Marketing'
                       AND c.chamado_principal_id IS NULL
                       AND op.nome <> 'Admin Marketing'
                       AND c.resolvido_em IS NOT NULL
                     GROUP BY 1, 2
                  ) t
                 GROUP BY 1, 2
                 ORDER BY 1, 2
                """
            )
            # Resolvidos do mês que NÃO aparecem em barra nenhuma: sem operador
            # atribuído ou fechados pela conta genérica. Existem de verdade (3
            # em julho/26) e são exatamente a diferença entre a soma das barras
            # e as "Concluídas" do mês — o card mostra isso pra conta fechar na
            # tela, em vez de deixar o usuário procurando o furo.
            resolvidos_orfaos_rows = await conn.fetch(
                """
                SELECT date_trunc('month', c.resolvido_em AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
                       count(*) AS n
                  FROM chamados c
                  JOIN departamentos d ON d.id = c.departamento_id
                  LEFT JOIN perfis op ON op.id = c.operador_id
                 WHERE d.nome = 'Marketing'
                   AND c.chamado_principal_id IS NULL
                   AND c.resolvido_em IS NOT NULL
                   AND (op.nome IS NULL OR op.nome = 'Admin Marketing')
                 GROUP BY 1
                """
            )

        volume_por_mes = {r["mes"]: dict(r) for r in volume_rows}
        midia_list = [dict(r) for r in midia_rows]
        baseline_por_mes = {r["mes"]: dict(r) for r in baseline_rows}

        # Todo mês com chamado OU baseline histórico entra na série (mesmo sem
        # chamado nenhum — mês pré-sistema só com baseline, etc.).
        #
        # 🔁 `[2026-08-10]` — `marketing_midia_regional` NÃO entra mais nessa
        # união (bug real reportado pelo usuário): o upload em massa da
        # planilha "Investimento por Região" (`ingestao_marketing_midia.py`)
        # trouxe uma aba por mês desde nov/2024, e cada mês reconhecido virou
        # linha na tabela — inclusive anos sem chamado nenhum no Portal. Como
        # a união incluía `marketing_midia_regional`, TODOS os indicadores
        # baseados em `todos_meses` (Status das Demandas, ranking por setor,
        # por operador, órfãos) passaram a "esticar" o eixo até nov/2024 com
        # meses vazios, mesmo não tendo relação nenhuma com mídia regional. O
        # indicador de Mídia Regional em si (Investimento BD, Regiões Ativas,
        # Descontinuidades, Aderências — `midia_final` abaixo) **não** usa
        # `todos_meses`: ele monta o próprio eixo direto de `midia_list`,
        # então continua mostrando o histórico completo (correto, pedido do
        # usuário) sem qualquer mudança.
        todos_meses = sorted(set(volume_por_mes) | set(baseline_por_mes))

        monthly_list = []
        dept_by_month: dict[str, dict[str, int]] = {}
        operador_by_month: dict[str, dict[str, dict[str, int]]] = {}
        resolvidos_orfaos_by_month: dict[str, int] = {}
        for mes in todos_meses:
            label = self._mes_label(mes)
            # Baseline tem prioridade sobre a view — ver docstring do método.
            v = baseline_por_mes.get(mes) or volume_por_mes.get(mes)
            total = v["total"] if v else 0
            concluidas = v["concluidas"] if v else 0
            mkt_orig = v["mkt_orig"] if v else 0
            sol_orig = v["sol_orig"] if v else 0
            # Quantas demandas ENTRARAM no mês (`created_at`) — o que era o
            # `total` até a migration `0081`. Serve de denominador para o card
            # de atrasos, a única quebra que ficou no cohort de abertura (a
            # lista `atrasosData` é montada por mês de abertura). O baseline
            # não separa os dois cohorts — a planilha do time traz um número
            # só por mês —, então lá `aberturas` é o próprio `total`.
            aberturas = v.get("aberturas", total) if v else 0
            monthly_list.append({
                "label": label,
                "total": total,
                "concluidas": concluidas,
                "em_andamento": v["em_andamento"] if v else 0,
                "abertas": v["abertas"] if v else 0,
                "volume": v["volume"] if v else 0,
                "mkt_orig": mkt_orig,
                "sol_orig": sol_orig,
                "aberturas": aberturas,
                # None (não 0.0) quando não há concluída nesse mês: "0 dias" é um
                # dado real diferente de "sem dado" — 0.0 fazia o gráfico de linha
                # cair pra zero em vez de mostrar a lacuna (o front trata null como
                # ponto ausente, ver `admin_marketing.js`).
                "tempo_medio": float(v["tempo_medio"]) if v and v["tempo_medio"] is not None else None,
                "atrasos": v["atrasos"] if v else 0,
                "pct_conc": round(100.0 * concluidas / total, 1) if total else 0.0,
                "pct_mkt": round(100.0 * mkt_orig / total, 1) if total else 0.0,
                # Mês cujos números vêm do histórico pré-sistema, não de
                # chamado do Portal. O front usa isso pra avisar onde as
                # quebras que dependem de ticket (quem atendeu, setor
                # solicitante) não têm como existir — jan-jun/26 TEM alguns
                # chamados reais soltos, então "operador vazio" não é sinal
                # confiável de mês pré-sistema.
                "baseline": mes in baseline_por_mes,
            })
            # Ranking por setor solicitante. Mesma regra de substituição do
            # volume: quando existe linha de baseline pro mês
            # (`marketing_setor_baseline`, migration `0075`), ela SUBSTITUI a
            # quebra da view — o histórico pré-Portal tem setor atribuído de
            # verdade (planilha do time), e a view só enxergaria os poucos
            # chamados soltos do período. Sem baseline, 100% da view, como
            # sempre; mês novo entra sozinho a cada chamado.
            #
            # 🔁 2026-08-03: a versão anterior forçava `Marketing = mkt_orig`
            # aqui, para o ranking bater com a aba "3. Origem da Demanda".
            # Isso caiu: as duas colunas da planilha medem coisas diferentes
            # (**Departamento** = setor para quem a peça foi feita ×
            # **Origem** = de quem partiu a iniciativa) e divergem em 4 dos 6
            # meses históricos — em jan/26, 8 contra 15. Coincidem nos meses de
            # Portal porque lá a origem é derivada da etiqueta de setor. O
            # ranking volta a usar o setor de verdade, que é o que o título
            # promete, e a aba mostra os dois números lado a lado.
            setores_baseline = {
                r["setor"]: r["total"] for r in setor_baseline_rows if r["mes"] == mes
            }
            dept_by_month[label] = setores_baseline or {
                r["setor"]: r["total"] for r in setor_rows if r["mes"] == mes
            }
            # Mesma limitação do ranking por setor: quem atendeu só existe em
            # chamado real do Portal. Os meses de baseline (jan-jun/26) ficam
            # com o dicionário vazio, e o front avisa na própria aba.
            operador_by_month[label] = {
                r["operador"]: {"atendidos": r["atendidos"], "resolvidos": r["resolvidos"]}
                for r in operador_rows
                if r["mes"] == mes
            }
            resolvidos_orfaos_by_month[label] = next(
                (r["n"] for r in resolvidos_orfaos_rows if r["mes"] == mes), 0
            )

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
            "operadorByMonth": operador_by_month,
            "resolvidosSemOperadorByMonth": resolvidos_orfaos_by_month,
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
        """Dado bruto para relatório. Diferente dos KPIs, **mantém** os chamados
        combinados (0065) e marca de qual principal eles são na coluna
        ``combinado_com`` — quem analisa a planilha decide se filtra ou não, e o
        tamanho real do incidente (quantas pessoas foram afetadas) não se perde.
        """
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.codigo, c.titulo, c.descricao, c.status, c.prioridade,
                       dep.nome AS departamento, cat.nome AS categoria, sub.nome AS subcategoria,
                       autor.nome AS solicitante, op.nome AS operador,
                       c.created_at, c.limite_resolucao, c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota, c.avaliacao_em, c.avaliacao_comentario,
                       princ.codigo AS combinado_com
                  FROM chamados c
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN subcategorias sub ON sub.id = c.subcategoria_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                  LEFT JOIN perfis op ON op.id = c.operador_id
                  LEFT JOIN chamados princ ON princ.id = c.chamado_principal_id
                 ORDER BY c.created_at DESC
                """
            )
            return [dict(r) for r in rows]


_admin_repo = AdminRepo()


def get_admin_repo() -> AdminRepo:
    return _admin_repo

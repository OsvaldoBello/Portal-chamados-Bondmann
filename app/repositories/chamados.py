"""Repositório de chamados — acesso de domínio sob RLS (Seções 3.1 / 5.1).

Cada método abre uma transação curta via :func:`rls_connection`, injetando os
claims do usuário (``SET LOCAL ROLE authenticated`` + ``request.jwt.claims``).
Toda autorização/isolamento multi-tenant é imposta pelo RLS no banco — as
queries aqui não recriam regra de negócio, apenas leem/escrevem o que o papel
do usuário tem permissão de ver.

O repositório é resolvido por dependência (:func:`get_chamados_repo`) para que
as rotas possam ser testadas com um fake, sem banco vivo.
"""

from __future__ import annotations

import json
from typing import Any

from app import cache
from app.db import rls_connection

PRIORIDADES = ("BAIXA", "MEDIA", "ALTA", "URGENTE")

# Chaves e TTL do cache de catálogos globais (Seção 2.3). Invalidados na escrita
# pelas rotas de admin (ver app/routes/admin.py).
CACHE_CATEGORIAS = "categorias_ativas"
CACHE_DEPARTAMENTOS = "departamentos_ativos"
CACHE_SUBCATEGORIAS = "subcategorias_ativas"  # sufixado por categoria_id
CATALOGO_TTL = 90.0  # segundos
NOTA_MIN, NOTA_MAX = 1, 5


class ChamadosRepo:
    """Operações de leitura/escrita de chamados em nome do usuário autenticado."""

    async def perfil(self, claims: dict) -> dict[str, Any] | None:
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """SELECT p.id, p.nome, p.role, p.empresa_id, p.departamento_id,
                          p.avatar_path, p.updated_at AS avatar_atualizado_em,
                          d.nome AS departamento,
                          COALESCE(d.nome = 'TI', false) AS is_ti
                     FROM perfis p
                     LEFT JOIN departamentos d ON d.id = p.departamento_id
                    WHERE p.id = $1::uuid""",
                claims["sub"],
            )
            return dict(row) if row else None

    async def atualizar_avatar(self, claims: dict, *, avatar_path: str | None) -> None:
        """Grava o path do avatar do PRÓPRIO usuário (Fase 7). RLS nova
        (`perfis_update_self`, migration 0033) só deixa autenticado atualizar o
        próprio perfil, e o trigger `enforce_perfil_self_so_avatar` restringe essa
        escrita à coluna `avatar_path` — o path em si é sempre
        `{auth.uid()}/avatar.<ext>` (`app/avatar_storage.py`), então não há como
        um usuário apontar para o avatar de outro."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE perfis SET avatar_path = $2 WHERE id = $1::uuid",
                claims["sub"],
                avatar_path,
            )

    async def listar(self, claims: dict, *, limite: int = 100) -> list[dict[str, Any]]:
        """"Meus chamados": os que o usuário ABRIU (portal do solicitante).

        Filtra por `cliente_id = auth.uid()` para que também o staff (que via RLS
        enxerga a fila do seu setor) veja aqui apenas os próprios. A fila de
        atendimento por departamento é o Workspace (Fase 4)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status, c.prioridade,
                       c.created_at, c.limite_resolucao, c.avaliacao_nota,
                       cat.nome AS categoria, dep.nome AS departamento
                  FROM chamados c
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                 WHERE c.cliente_id = $1::uuid
                 ORDER BY c.created_at DESC
                 LIMIT $2
                """,
                claims["sub"],
                limite,
            )
            return [dict(r) for r in rows]

    async def stats(self, claims: dict) -> dict[str, int]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT status, count(*) AS n FROM chamados WHERE cliente_id = $1::uuid GROUP BY status",
                claims["sub"],
            )
        por_status = {r["status"]: r["n"] for r in rows}
        return {
            "total": sum(por_status.values()),
            "novo": por_status.get("NOVO", 0),
            "em_atendimento": por_status.get("EM_ATENDIMENTO", 0),
            "aguardando": por_status.get("AGUARDANDO", 0),
            "resolvido": por_status.get("RESOLVIDO", 0),
        }

    async def obter(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                SELECT c.id, c.codigo, c.titulo, c.descricao, c.status, c.prioridade,
                       c.cliente_id, c.operador_id, c.departamento_id, c.data_entrega,
                       c.created_at, c.limite_resposta, c.limite_resolucao,
                       c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota, c.avaliacao_comentario, c.avaliacao_em,
                       c.volume, c.origem_demanda, c.causa_atraso,
                       cat.nome AS categoria, sub.nome AS subcategoria,
                       dep.nome AS departamento,
                       autor.nome AS cliente_nome, autor.avatar_path AS cliente_avatar_path,
                       autor.updated_at AS cliente_avatar_atualizado_em,
                       op.nome AS operador_nome
                  FROM chamados c
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN subcategorias sub ON sub.id = c.subcategoria_id
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                  LEFT JOIN perfis op ON op.id = c.operador_id
                 WHERE c.id = $1::uuid
                """,
                chamado_id,
            )
            return dict(row) if row else None

    async def mensagens(self, claims: dict, chamado_id: str) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT m.id, m.conteudo, m.is_interna, m.created_at, m.anexos,
                       p.nome AS remetente_nome, p.role AS remetente_role
                  FROM mensagens m
                  LEFT JOIN perfis p ON p.id = m.remetente_id
                 WHERE m.chamado_id = $1::uuid
                 ORDER BY m.created_at ASC
                """,
                chamado_id,
            )
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                # asyncpg devolve jsonb como texto; normaliza para lista de anexos.
                bruto = d.get("anexos")
                d["anexos"] = json.loads(bruto) if isinstance(bruto, str) else (bruto or [])
                out.append(d)
            return out

    async def categorias_ativas(
        self, claims: dict, departamento_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Categorias ativas, opcionalmente filtradas por departamento.

        Categorias pertencem a um departamento (migration 0019); a abertura mostra
        só as do setor de destino escolhido. Cacheado por departamento."""
        chave = f"{CACHE_CATEGORIAS}:{departamento_id or 'all'}"
        cached = cache.get(chave)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT id, nome FROM categorias
                    WHERE ativo = true
                      AND ($1::uuid IS NULL OR departamento_id = $1::uuid)
                    ORDER BY nome""",
                departamento_id,
            )
        resultado = [dict(r) for r in rows]
        cache.set(chave, resultado, CATALOGO_TTL)
        return resultado

    async def categoria_valida(
        self, claims: dict, *, categoria_id: str, departamento_id: str
    ) -> bool:
        """A categoria existe, está ativa e pertence ao departamento informado?
        Defesa em profundidade contra POST com par categoria/departamento forjado."""
        cats = await self.categorias_ativas(claims, departamento_id)
        return any(str(c["id"]) == str(categoria_id) for c in cats)

    async def departamentos_ativos(self, claims: dict) -> list[dict[str, Any]]:
        """Todos os setores ativos da empresa (catálogo unificado — 0027). Cada um
        traz ``recebe_chamados``: só os que têm fila de atendimento (TI/RH/Marketing,
        hoje) podem ser destino de chamado; os demais só identificam quem abriu."""
        cached = cache.get(CACHE_DEPARTAMENTOS)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT id, nome, recebe_chamados FROM departamentos WHERE ativo = true ORDER BY nome"
            )
        resultado = [dict(r) for r in rows]
        cache.set(CACHE_DEPARTAMENTOS, resultado, CATALOGO_TTL)
        return resultado

    async def departamentos_destino_ativos(self, claims: dict) -> list[dict[str, Any]]:
        """Subconjunto de :meth:`departamentos_ativos` que pode ser destino de
        chamado (``recebe_chamados``) — usado no dropdown "Departamento de destino"
        e no repasse de setor (Workspace)."""
        todos = await self.departamentos_ativos(claims)
        return [d for d in todos if d.get("recebe_chamados")]

    async def subcategorias_ativas(
        self, claims: dict, categoria_id: str
    ) -> list[dict[str, Any]]:
        """Subcategorias ativas de uma categoria (cascade da abertura de chamado).

        Cacheado por categoria (chave sufixada por ``categoria_id``). Retorna ``[]``
        para categoria inexistente/sem subcategorias — o form trata o vazio."""
        chave = f"{CACHE_SUBCATEGORIAS}:{categoria_id}"
        cached = cache.get(chave)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT id, nome FROM subcategorias
                    WHERE categoria_id = $1::uuid AND ativo = true
                    ORDER BY nome""",
                categoria_id,
            )
        resultado = [dict(r) for r in rows]
        cache.set(chave, resultado, CATALOGO_TTL)
        return resultado

    async def subcategoria_valida(
        self, claims: dict, *, categoria_id: str, subcategoria_id: str
    ) -> bool:
        """A subcategoria existe, está ativa e pertence à categoria informada?
        Defesa em profundidade contra POST com par categoria/subcategoria forjado."""
        subs = await self.subcategorias_ativas(claims, categoria_id)
        return any(str(s["id"]) == str(subcategoria_id) for s in subs)

    async def criar(
        self,
        claims: dict,
        *,
        empresa_id: str,
        cliente_id: str,
        categoria_id: str | None,
        subcategoria_id: str | None,
        departamento_id: str,
        titulo: str,
        descricao: str,
        prioridade: str,
        setor: str,
        data_entrega: "date | None" = None,
        volume: int = 1,
        origem_demanda: str = "Solicitação",
    ) -> dict[str, Any]:
        """Cria um chamado endereçado a um departamento. Código/SLA via triggers.

        ``data_entrega`` (fluxo por demanda do Marketing) define o prazo de SLA
        diretamente — o trigger ``calcular_sla_chamado`` usa a data em vez da
        prioridade quando ela é informada (migration 0022)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO chamados
                    (empresa_id, cliente_id, categoria_id, subcategoria_id, departamento_id,
                     titulo, descricao, prioridade, data_entrega, setor, volume, origem_demanda)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7,
                        $8::prioridade_chamado, $9::date, $10, $11::integer, $12)
                RETURNING id, codigo
                """,
                empresa_id,
                cliente_id,
                categoria_id,
                subcategoria_id,
                departamento_id,
                titulo,
                descricao,
                prioridade,
                data_entrega,
                setor,
                volume,
                origem_demanda,
            )
            return dict(row)

    async def avaliar(
        self, claims: dict, chamado_id: str, *, nota: int, comentario: str | None
    ) -> dict[str, Any] | None:
        """Registra a avaliação 1–5 do autor (RLS exige RESOLVIDO + próprio)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET avaliacao_nota = $2,
                       avaliacao_comentario = $3,
                       avaliacao_em = now()
                 WHERE id = $1::uuid
             RETURNING id, avaliacao_nota, avaliacao_comentario, avaliacao_em
                """,
                chamado_id,
                nota,
                comentario,
            )
            if row is not None:
                await conn.execute(
                    """
                    INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
                    VALUES ($1::uuid, $2::uuid, 'AVALIADO',
                            jsonb_build_object('nota', $3::int))
                    """,
                    chamado_id,
                    claims["sub"],
                    nota,
                )
            return dict(row) if row else None

    async def adicionar_mensagem(
        self,
        claims: dict,
        chamado_id: str,
        *,
        remetente_id: str,
        conteudo: str,
        anexos: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Insere mensagem pública com anexos opcionais.

        ``anexos`` é a lista de metadados ``{path, nome, mime, tamanho}`` (os bytes
        já foram enviados ao Storage privado). Persiste como ``jsonb``.
        """
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos)
                VALUES ($1::uuid, $2::uuid, $3, false, $4::jsonb)
                RETURNING id, created_at
                """,
                chamado_id,
                remetente_id,
                conteudo,
                json.dumps(anexos or []),
            )
            return dict(row)

    # ---------------------------------------------------------------------
    # Workspace do operador (Fase 4) — escopo por departamento via RLS.
    #
    # 🔁 Fase 1 (2026-07-09): a fila/kanban do setor deixou de misturar três
    # coisas diferentes. Três recortes explícitos, sem depender só da RLS:
    #   - fila()/kanban (a atender): chamados do MEU setor abertos por quem
    #     NÃO é colega do mesmo departamento (nem eu mesmo) — pedido "de fora".
    #   - listar() ("Meus chamados"): os que EU mesmo abri, pra qualquer setor.
    #   - chamados_departamento() (novo): chamados abertos por OUTRO colega do
    #     MEU setor de origem, para QUALQUER departamento de destino (mesmo
    #     recorte de leitura da RLS de líder de setor, migration 0028).
    # ---------------------------------------------------------------------
    _FILA_COLUNAS = """
        SELECT c.id, c.codigo, c.titulo, c.status, c.prioridade, c.setor,
               c.created_at, c.limite_resolucao, c.respondido_em, c.resolvido_em,
               cat.nome AS categoria, dep.nome AS departamento,
               autor.nome AS cliente_nome, autor.avatar_path AS cliente_avatar_path,
               autor.updated_at AS cliente_avatar_atualizado_em,
               op.nome AS operador_nome
          FROM chamados c
          LEFT JOIN categorias cat ON cat.id = c.categoria_id
          LEFT JOIN departamentos dep ON dep.id = c.departamento_id
          LEFT JOIN perfis autor ON autor.id = c.cliente_id
          LEFT JOIN perfis op ON op.id = c.operador_id
    """

    async def fila(
        self,
        claims: dict,
        *,
        departamento_id: str | None,
        status: str | None = None,
        categoria_id: str | None = None,
        prioridade: str | None = None,
        operador_id: str | None = None,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        """Fila/Kanban (a atender): chamados do MEU setor abertos por alguém que
        NÃO é colega do mesmo departamento nem eu mesmo — pedidos "de fora" do
        setor (o público que a fila existe para atender). Chamados abertos por um
        colega ficam em :meth:`chamados_departamento`; os meus, em :meth:`listar`.

        Filtros opcionais: ``status``, ``categoria_id``, ``prioridade`` e
        ``operador_id`` (o filtro de SLA é aplicado na camada de rota, pois depende
        do cálculo de estado do domínio). **Ordenação padrão: data de abertura
        (mais recentes primeiro).**
        """
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                self._FILA_COLUNAS
                + """
                 WHERE ($6::uuid IS NULL OR c.departamento_id = $6::uuid)
                   AND c.cliente_id <> auth.uid()
                   AND autor.departamento_id IS DISTINCT FROM c.departamento_id
                   AND ($1::status_chamado IS NULL OR c.status = $1::status_chamado)
                   AND ($3::uuid IS NULL OR c.categoria_id = $3::uuid)
                   AND ($4::prioridade_chamado IS NULL OR c.prioridade = $4::prioridade_chamado)
                   AND ($5::uuid IS NULL OR c.operador_id = $5::uuid)
                 ORDER BY c.created_at DESC
                 LIMIT $2
                """,
                status,
                limite,
                categoria_id,
                prioridade,
                operador_id,
                departamento_id,
            )
            return [dict(r) for r in rows]

    async def chamados_departamento(
        self,
        claims: dict,
        *,
        departamento_id: str | None,
        status: str | None = None,
        categoria_id: str | None = None,
        prioridade: str | None = None,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        """"Chamados do Departamento" (Fase 1): chamados abertos por OUTRO colega
        do MEU setor de origem (não eu) — independente do departamento de
        DESTINO do chamado. O recorte é sobre o setor do AUTOR, não sobre para
        onde o chamado foi roteado: um colega de TI que abre um chamado para o
        RH (ex.: solicitação de férias) ainda aparece aqui para o líder/colega
        de TI acompanhar — mesmo escopo de leitura que a RLS de "líder de
        setor" (migration 0028) já concede; quem não tem esse privilégio
        simplesmente não recebe essas linhas (RLS). Claim/resposta seguem a
        mesma trava de segregação de função da Fase 0 (autor nunca atende o
        próprio chamado, mesmo sendo colega de quem está vendo esta lista)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                self._FILA_COLUNAS
                + """
                 WHERE ($5::uuid IS NULL OR autor.departamento_id = $5::uuid)
                   AND c.cliente_id <> auth.uid()
                   AND ($1::status_chamado IS NULL OR c.status = $1::status_chamado)
                   AND ($3::uuid IS NULL OR c.categoria_id = $3::uuid)
                   AND ($4::prioridade_chamado IS NULL OR c.prioridade = $4::prioridade_chamado)
                 ORDER BY c.created_at DESC
                 LIMIT $2
                """,
                status,
                limite,
                categoria_id,
                prioridade,
                departamento_id,
            )
            return [dict(r) for r in rows]

    async def fila_assinatura(
        self, claims: dict, *, departamento_id: str | None, status: str | None = None
    ):
        """Assinatura leve da fila (count + max ``updated_at``) no escopo do staff.

        Usada para ETag/304 no polling: se nada mudou, evita buscar todas as linhas
        e re-renderizar o fragmento (Seção 2.2). Escopo alinhado ao de :meth:`fila`
        (chamados "de fora" do setor); os demais filtros entram no ETag pela rota."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """SELECT count(*)::int AS n, max(c.updated_at) AS mx
                     FROM chamados c
                     LEFT JOIN perfis autor ON autor.id = c.cliente_id
                    WHERE ($2::uuid IS NULL OR c.departamento_id = $2::uuid)
                      AND c.cliente_id <> auth.uid()
                      AND autor.departamento_id IS DISTINCT FROM c.departamento_id
                      AND ($1::status_chamado IS NULL OR c.status = $1::status_chamado)""",
                status,
                departamento_id,
            )
            return (row["n"], row["mx"])

    async def fila_stats(self, claims: dict, *, departamento_id: str | None = None) -> dict[str, int]:
        """Contagem por status no escopo da fila/kanban (cabeçalhos), mesmo
        recorte de :meth:`fila` — não conta chamados próprios nem de colegas."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT c.status, count(*) AS n
                     FROM chamados c
                     LEFT JOIN perfis autor ON autor.id = c.cliente_id
                    WHERE ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                      AND c.cliente_id <> auth.uid()
                      AND autor.departamento_id IS DISTINCT FROM c.departamento_id
                    GROUP BY c.status""",
                departamento_id,
            )
        por = {r["status"]: r["n"] for r in rows}
        return {
            "total": sum(por.values()),
            "NOVO": por.get("NOVO", 0),
            "EM_ATENDIMENTO": por.get("EM_ATENDIMENTO", 0),
            "AGUARDANDO": por.get("AGUARDANDO", 0),
            "RESOLVIDO": por.get("RESOLVIDO", 0),
        }

    async def operadores(
        self, claims: dict, *, departamento_id: str | None = None, excluir_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Staff disponível para atribuição (role OPERADOR/ADMIN).

        Quando ``departamento_id`` é informado, retorna **apenas** o staff daquele
        setor — a atribuição de responsável é sempre dentro do departamento do
        chamado (RH atribui RH; TI, ao atender um chamado de RH, atribui RH).
        A troca de setor é uma ação separada (só TI — ver :meth:`transferir`).
        ``excluir_id`` tira um usuário específico da lista — usado para excluir o
        autor do chamado (autor nunca é o próprio responsável)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT p.id, p.nome, d.nome AS departamento
                  FROM perfis p LEFT JOIN departamentos d ON d.id = p.departamento_id
                 WHERE p.role IN ('OPERADOR','ADMIN') AND p.ativo
                   AND ($1::uuid IS NULL OR p.departamento_id = $1::uuid)
                   AND ($2::uuid IS NULL OR p.id <> $2::uuid)
                 ORDER BY p.nome
                """,
                departamento_id,
                excluir_id,
            )
            return [dict(r) for r in rows]

    async def iniciar_atendimento(
        self, claims: dict, chamado_id: str, *, operador_id: str
    ) -> dict[str, Any] | None:
        """Inicia o atendimento: move NOVO→EM_ATENDIMENTO e assume como responsável.

        Idempotente: só age quando o chamado está em ``NOVO`` (senão devolve None).
        Segregação de função: o autor do chamado nunca pode assumir o próprio
        chamado, mesmo sendo staff do setor de destino (devolve None). Registra
        ``ATENDIMENTO_INICIADO`` no histórico. Escopo por RLS (staff)."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchrow(
                "SELECT status, cliente_id FROM chamados WHERE id = $1::uuid", chamado_id
            )
            if atual is None or atual["status"] != "NOVO":
                return None
            if str(atual["cliente_id"]) == str(operador_id):
                return None
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET status = 'EM_ATENDIMENTO'::status_chamado,
                       operador_id = $2::uuid
                 WHERE id = $1::uuid AND status = 'NOVO'::status_chamado
                   AND cliente_id <> $2::uuid
             RETURNING id, status, operador_id
                """,
                chamado_id,
                operador_id,
            )
            if row is None:
                return None
            await self._registrar(
                conn, chamado_id, claims["sub"], "ATENDIMENTO_INICIADO",
                {"operador_id": operador_id},
            )
            return dict(row)

    async def transferir(
        self, claims: dict, chamado_id: str, *, departamento_id: str
    ) -> dict[str, Any] | None:
        """Repassa o chamado para outro departamento (só TI — imposto pela RLS
        `chamados_update_staff`: WITH CHECK exige `auth_is_ti()` para gravar um
        `departamento_id` fora do setor do usuário). Limpa o operador (era do setor
        antigo) e registra `DEPARTAMENTO_ALTERADO`. Devolve None se nada mudou/fora
        do escopo."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchval(
                "SELECT departamento_id FROM chamados WHERE id = $1::uuid", chamado_id
            )
            if atual is None or str(atual) == str(departamento_id):
                return None
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET departamento_id = $2::uuid, operador_id = NULL
                 WHERE id = $1::uuid
             RETURNING id, departamento_id
                """,
                chamado_id,
                departamento_id,
            )
            if row is None:
                return None
            await self._registrar(
                conn, chamado_id, claims["sub"], "DEPARTAMENTO_ALTERADO",
                {"de": str(atual), "para": str(departamento_id)},
            )
            return dict(row)

    async def _registrar(
        self, conn, chamado_id: str, ator_id: str, acao: str, detalhes: dict
    ) -> None:
        await conn.execute(
            """
            INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)
            """,
            chamado_id,
            ator_id,
            acao,
            json.dumps(detalhes),
        )

    async def alterar_status(
        self, claims: dict, chamado_id: str, novo_status: str
    ) -> dict[str, Any] | None:
        """Altera o status (staff no escopo). Marca `resolvido_em` ao resolver e
        registra no histórico. Retorna o chamado atualizado ou None (fora do escopo)."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchval("SELECT status FROM chamados WHERE id = $1::uuid", chamado_id)
            if atual is None or atual == novo_status:
                return None
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET status = $2::status_chamado,
                       resolvido_em = CASE WHEN $2 = 'RESOLVIDO' THEN now()
                                           WHEN $2 <> 'RESOLVIDO' THEN NULL
                                           ELSE resolvido_em END
                 WHERE id = $1::uuid
             RETURNING id, status
                """,
                chamado_id,
                novo_status,
            )
            if row is None:
                return None
            await self._registrar(
                conn, chamado_id, claims["sub"], "STATUS_ALTERADO",
                {"de": atual, "para": novo_status},
            )
            return dict(row)

    async def alterar_prioridade(
        self, claims: dict, chamado_id: str, nova_prioridade: str
    ) -> dict[str, Any] | None:
        """Altera a prioridade (o trigger recalcula os prazos de SLA) + histórico."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchval("SELECT prioridade FROM chamados WHERE id = $1::uuid", chamado_id)
            if atual is None or atual == nova_prioridade:
                return None
            row = await conn.fetchrow(
                """
                UPDATE chamados SET prioridade = $2::prioridade_chamado
                 WHERE id = $1::uuid RETURNING id, prioridade
                """,
                chamado_id,
                nova_prioridade,
            )
            if row is None:
                return None
            await self._registrar(
                conn, chamado_id, claims["sub"], "PRIORIDADE_ALTERADA",
                {"de": atual, "para": nova_prioridade},
            )
            return dict(row)

    async def atribuir(
        self, claims: dict, chamado_id: str, operador_id: str | None
    ) -> dict[str, Any] | None:
        """Atribui (ou remove) o operador responsável + histórico.

        Segregação de função: não deixa atribuir o autor do chamado como o
        próprio responsável (devolve None nesse caso — a UI já não lista o
        autor entre os operadores, isto é defesa em profundidade)."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchrow("SELECT cliente_id FROM chamados WHERE id = $1::uuid", chamado_id)
            if atual is None:
                return None
            if operador_id and str(atual["cliente_id"]) == str(operador_id):
                return None
            row = await conn.fetchrow(
                "UPDATE chamados SET operador_id = $2::uuid WHERE id = $1::uuid RETURNING id, operador_id",
                chamado_id,
                operador_id,
            )
            if row is None:
                return None
            await self._registrar(
                conn, chamado_id, claims["sub"], "ATRIBUIDO",
                {"operador_id": operador_id},
            )
            return dict(row)

    async def excluir(self, claims: dict, chamado_id: str) -> bool:
        """Exclui definitivamente o chamado (operador/admin do setor, ou TI —
        RLS `chamados_delete_staff`, migration 0025). `mensagens` e
        `historico_chamados` somem junto via FK `ON DELETE CASCADE`. Devolve
        ``False`` se o chamado não existe ou está fora do escopo do usuário."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                "DELETE FROM chamados WHERE id = $1::uuid RETURNING id", chamado_id
            )
            return row is not None

    async def responder_staff(
        self,
        claims: dict,
        chamado_id: str,
        *,
        conteudo: str,
        is_interna: bool,
        anexos: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Mensagem do staff (pública ou nota interna), com anexos opcionais.

        ``anexos`` são os metadados ``{path, nome, mime, tamanho}`` (bytes já no
        Storage privado). Na 1ª resposta pública, marca `respondido_em`
        (conformidade do SLA de resposta)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos)
                VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb)
                RETURNING id, created_at
                """,
                chamado_id,
                claims["sub"],
                conteudo,
                is_interna,
                json.dumps(anexos or []),
            )
            if not is_interna:
                await conn.execute(
                    "UPDATE chamados SET respondido_em = now() WHERE id = $1::uuid AND respondido_em IS NULL",
                    chamado_id,
                )
            return dict(row) if row else None

    async def notificacoes(self, claims: dict, *, limite: int = 6) -> list[dict[str, Any]]:
        """Itens que precisam de atenção no escopo do usuário (o escopo vem da RLS):
        chamados **não resolvidos** + **resolvidos-não-avaliados** do próprio autor.
        Serve tanto ao sino do staff (fila do setor) quanto ao do funcionário."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status,
                       c.created_at, c.limite_resolucao, c.resolvido_em,
                       c.avaliacao_nota, (c.cliente_id = auth.uid()) AS meu
                  FROM chamados c
                 WHERE c.status <> 'RESOLVIDO'
                    OR (c.resolvido_em IS NOT NULL AND c.avaliacao_nota IS NULL
                        AND c.cliente_id = auth.uid())
                 ORDER BY COALESCE(c.updated_at, c.created_at) DESC
                 LIMIT $1
                """,
                limite,
            )
            return [dict(r) for r in rows]

    async def salvar_marketing_meta(
        self, claims: dict, chamado_id: str, *, volume: int, origem_demanda: str, causa_atraso: str | None
    ) -> dict[str, Any] | None:
        """Salva as informações de volume, origem da demanda e causa de atraso (staff no escopo)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET volume = $2::integer,
                       origem_demanda = $3,
                       causa_atraso = $4
                 WHERE id = $1::uuid
             RETURNING id, volume, origem_demanda, causa_atraso
                """,
                chamado_id,
                volume,
                origem_demanda,
                causa_atraso,
            )
            if row is not None:
                await self._registrar(
                    conn, chamado_id, claims["sub"], "MARKETING_META_ALTERADO",
                    {"volume": volume, "origem_demanda": origem_demanda, "causa_atraso": causa_atraso},
                )
            return dict(row) if row else None

    # ---------------------------------------------------------------------
    # "Em cópia" — observadores multi-setoriais (Fase 8, 2026-07-09).
    # ---------------------------------------------------------------------
    async def usuarios_para_copia(
        self, claims: dict, *, excluir_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Usuários ativos de QUALQUER setor, para o seletor "Adicionar em
        cópia" — diferente de :meth:`operadores` (só staff do setor do
        chamado), aqui é qualquer pessoa da organização (multi-setorial)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT p.id, p.nome, d.nome AS departamento
                  FROM perfis p LEFT JOIN departamentos d ON d.id = p.departamento_id
                 WHERE p.ativo AND ($1::uuid IS NULL OR p.id <> $1::uuid)
                 ORDER BY p.nome
                """,
                excluir_id,
            )
            return [dict(r) for r in rows]

    async def observadores(self, claims: dict, chamado_id: str) -> list[dict[str, Any]]:
        """Quem está "em cópia" no chamado (RLS: só quem já enxerga o chamado
        vê a lista — mesma regra de quem pode adicionar/remover)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT o.perfil_id, p.nome, d.nome AS departamento
                  FROM chamados_observadores o
                  JOIN perfis p ON p.id = o.perfil_id
                  LEFT JOIN departamentos d ON d.id = p.departamento_id
                 WHERE o.chamado_id = $1::uuid
                 ORDER BY p.nome
                """,
                chamado_id,
            )
            return [dict(r) for r in rows]

    async def adicionar_observador(
        self, claims: dict, chamado_id: str, perfil_id: str
    ) -> None:
        """Adiciona um observador (RLS restringe a quem já enxerga o chamado).
        Idempotente: reenviar o mesmo par não duplica nem falha."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                """
                INSERT INTO chamados_observadores (chamado_id, perfil_id, criado_por)
                VALUES ($1::uuid, $2::uuid, $3::uuid)
                ON CONFLICT (chamado_id, perfil_id) DO NOTHING
                """,
                chamado_id,
                perfil_id,
                claims["sub"],
            )

    async def remover_observador(
        self, claims: dict, chamado_id: str, perfil_id: str
    ) -> None:
        async with rls_connection(claims) as conn:
            await conn.execute(
                "DELETE FROM chamados_observadores WHERE chamado_id = $1::uuid AND perfil_id = $2::uuid",
                chamado_id,
                perfil_id,
            )


_repo = ChamadosRepo()


def get_chamados_repo() -> ChamadosRepo:
    """Dependência FastAPI; sobreposta nos testes por um fake."""
    return _repo


def validar_nota(raw: str | int | None) -> int:
    """Valida e normaliza a nota de avaliação (1–5). Levanta ``ValueError``."""
    try:
        nota = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("Nota inválida: informe um número de 1 a 5.")
    if not (NOTA_MIN <= nota <= NOTA_MAX):
        raise ValueError("Nota fora do intervalo: use de 1 a 5 estrelas.")
    return nota

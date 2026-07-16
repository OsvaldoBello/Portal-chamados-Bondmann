"""Repositório de atendimento — ciclo de vida do chamado (Seção 3.1/5.1).

Extraído de `ChamadosRepo` (Sprint 2 / item 2.1, M1). Mesma regra das demais
partes do domínio: cada método abre uma transação curta via
:func:`rls_connection`, RLS impõe autorização/isolamento.
"""

from __future__ import annotations

import json
from typing import Any

from app.db import rls_connection


class AtendimentoRepo:
    """Ciclo de vida do chamado: obter/criar/avaliar e ações de staff
    (iniciar, transferir, alterar status/prioridade, atribuir, excluir,
    metadados de Marketing)."""

    async def obter(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                SELECT c.id, c.codigo, c.titulo, c.descricao, c.status, c.prioridade,
                       c.cliente_id, c.operador_id, c.departamento_id, c.data_entrega,
                       c.sem_prazo,
                       c.created_at, c.limite_resposta, c.limite_resolucao,
                       c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota, c.avaliacao_comentario, c.avaliacao_em,
                       c.volume, c.origem_demanda, c.causa_atraso,
                       cat.nome AS categoria, sub.nome AS subcategoria,
                       dep.nome AS departamento, dep.autoatendimento,
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
        sem_prazo: bool = False,
    ) -> dict[str, Any]:
        """Cria um chamado endereçado a um departamento. Código/SLA via triggers.

        ``data_entrega`` (fluxo por demanda do Marketing) define o prazo de SLA
        diretamente — o trigger ``calcular_sla_chamado`` usa a data em vez da
        prioridade quando ela é informada (migration 0022). ``sem_prazo`` (0040)
        é o oposto: demanda sem urgência nem prazo, o trigger não calcula SLA
        nenhum (tem prioridade sobre ``data_entrega``)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO chamados
                    (empresa_id, cliente_id, categoria_id, subcategoria_id, departamento_id,
                     titulo, descricao, prioridade, data_entrega, setor, volume, origem_demanda,
                     sem_prazo)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7,
                        $8::prioridade_chamado, $9::date, $10, $11::integer, $12, $13::boolean)
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
                sem_prazo,
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

    async def iniciar_atendimento(
        self, claims: dict, chamado_id: str, *, operador_id: str, novo_status: str = "EM_ATENDIMENTO"
    ) -> dict[str, Any] | None:
        """Inicia o atendimento: move NOVO/A_FAZER→``novo_status`` e assume como responsável.

        ``novo_status`` normalmente é ``EM_ATENDIMENTO`` (botão "Iniciar
        atendimento"), mas o Kanban também chama isto para QUALQUER destino de
        drag a partir de ``NOVO``/``A_FAZER`` (ex.: arrastar direto pra
        "Aguardando", pulando "Em andamento") — sem isso, esse arraste só
        trocava o status e o chamado ficava andando no quadro sem responsável
        (bug real: BOND-2026-00035/00038, ambos foram parar em RESOLVIDO/
        AGUARDANDO com ``operador_id`` nulo).

        Idempotente: só age quando o chamado ainda não foi assumido (``NOVO`` ou
        ``A_FAZER`` — o Kanban do Marketing tem essa coluna intermediária antes de
        "Em atendimento"; senão devolve None). Segregação de função: o autor do
        chamado nunca pode assumir o próprio chamado, mesmo sendo staff do setor
        de destino (devolve None) — **exceto nos departamentos com
        autoatendimento** (``departamentos.autoatendimento`` — Marketing e RH,
        migrations 0038/0042), onde o próprio setor cria e gerencia as demandas
        (quadro estilo Trello, sem a lógica de suporte de TI). Registra
        ``ATENDIMENTO_INICIADO`` no histórico. Escopo por RLS (staff)."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchrow(
                """
                SELECT c.status, c.cliente_id, dep.autoatendimento
                  FROM chamados c
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                 WHERE c.id = $1::uuid
                """,
                chamado_id,
            )
            if atual is None or atual["status"] not in ("NOVO", "A_FAZER"):
                return None
            autoatendimento = bool(atual["autoatendimento"])
            if not autoatendimento and str(atual["cliente_id"]) == str(operador_id):
                return None
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET status = $4::status_chamado,
                       operador_id = $2::uuid
                 WHERE id = $1::uuid
                   AND status IN ('NOVO'::status_chamado, 'A_FAZER'::status_chamado)
                   AND ($3::boolean OR cliente_id <> $2::uuid)
             RETURNING id, status, operador_id
                """,
                chamado_id,
                operador_id,
                autoatendimento,
                novo_status,
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
        autor entre os operadores, isto é defesa em profundidade) — **exceto
        nos departamentos com autoatendimento** (Marketing e RH), onde o autor
        É o dono da própria demanda no quadro."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchrow(
                """
                SELECT c.cliente_id, dep.autoatendimento
                  FROM chamados c
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                 WHERE c.id = $1::uuid
                """,
                chamado_id,
            )
            if atual is None:
                return None
            autoatendimento = bool(atual["autoatendimento"])
            if operador_id and not autoatendimento and str(atual["cliente_id"]) == str(operador_id):
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

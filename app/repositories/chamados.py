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

from app.db import rls_connection

PRIORIDADES = ("BAIXA", "MEDIA", "ALTA", "URGENTE")
NOTA_MIN, NOTA_MAX = 1, 5


class ChamadosRepo:
    """Operações de leitura/escrita de chamados em nome do usuário autenticado."""

    async def perfil(self, claims: dict) -> dict[str, Any] | None:
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                "SELECT id, nome, role, empresa_id FROM perfis WHERE id = $1::uuid",
                claims["sub"],
            )
            return dict(row) if row else None

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
                       c.cliente_id, c.operador_id, c.departamento_id,
                       c.created_at, c.limite_resposta, c.limite_resolucao,
                       c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota, c.avaliacao_comentario, c.avaliacao_em,
                       cat.nome AS categoria, dep.nome AS departamento,
                       autor.nome AS cliente_nome, op.nome AS operador_nome
                  FROM chamados c
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
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

    async def categorias_ativas(self, claims: dict) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT id, nome FROM categorias WHERE ativo = true ORDER BY nome"
            )
            return [dict(r) for r in rows]

    async def departamentos_ativos(self, claims: dict) -> list[dict[str, Any]]:
        """Departamentos de destino disponíveis para abertura (TI/RH/Marketing)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT id, nome FROM departamentos WHERE ativo = true ORDER BY nome"
            )
            return [dict(r) for r in rows]

    async def criar(
        self,
        claims: dict,
        *,
        empresa_id: str,
        cliente_id: str,
        categoria_id: str | None,
        departamento_id: str,
        titulo: str,
        descricao: str,
        prioridade: str,
    ) -> dict[str, Any]:
        """Cria um chamado endereçado a um departamento. Código/SLA via triggers."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO chamados
                    (empresa_id, cliente_id, categoria_id, departamento_id,
                     titulo, descricao, prioridade)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, $7::prioridade_chamado)
                RETURNING id, codigo
                """,
                empresa_id,
                cliente_id,
                categoria_id,
                departamento_id,
                titulo,
                descricao,
                prioridade,
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
    # ---------------------------------------------------------------------
    async def fila(
        self, claims: dict, *, status: str | None = None, limite: int = 200
    ) -> list[dict[str, Any]]:
        """Fila de atendimento no escopo do staff (RLS: TI = tudo; RH/Mkt = seu setor).

        Ordena por prioridade (URGENTE→BAIXA) e prazo de resolução mais próximo.
        """
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status, c.prioridade,
                       c.created_at, c.limite_resolucao, c.respondido_em, c.resolvido_em,
                       cat.nome AS categoria, dep.nome AS departamento,
                       autor.nome AS cliente_nome, op.nome AS operador_nome
                  FROM chamados c
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                  LEFT JOIN perfis op ON op.id = c.operador_id
                 WHERE ($1::status_chamado IS NULL OR c.status = $1::status_chamado)
                 ORDER BY
                   CASE c.prioridade WHEN 'URGENTE' THEN 0 WHEN 'ALTA' THEN 1
                                     WHEN 'MEDIA' THEN 2 ELSE 3 END,
                   c.limite_resolucao ASC NULLS LAST
                 LIMIT $2
                """,
                status,
                limite,
            )
            return [dict(r) for r in rows]

    async def fila_stats(self, claims: dict) -> dict[str, int]:
        """Contagem por status no escopo do staff (para os cabeçalhos do Kanban)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch("SELECT status, count(*) AS n FROM chamados GROUP BY status")
        por = {r["status"]: r["n"] for r in rows}
        return {
            "total": sum(por.values()),
            "NOVO": por.get("NOVO", 0),
            "EM_ATENDIMENTO": por.get("EM_ATENDIMENTO", 0),
            "AGUARDANDO": por.get("AGUARDANDO", 0),
            "RESOLVIDO": por.get("RESOLVIDO", 0),
        }

    async def operadores(self, claims: dict) -> list[dict[str, Any]]:
        """Staff disponível para atribuição (role OPERADOR/ADMIN)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT p.id, p.nome, d.nome AS departamento
                  FROM perfis p LEFT JOIN departamentos d ON d.id = p.departamento_id
                 WHERE p.role IN ('OPERADOR','ADMIN') AND p.ativo
                 ORDER BY p.nome
                """
            )
            return [dict(r) for r in rows]

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
        """Atribui (ou remove) o operador responsável + histórico."""
        async with rls_connection(claims) as conn:
            existe = await conn.fetchval("SELECT 1 FROM chamados WHERE id = $1::uuid", chamado_id)
            if existe is None:
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

    async def responder_staff(
        self, claims: dict, chamado_id: str, *, conteudo: str, is_interna: bool
    ) -> dict[str, Any] | None:
        """Mensagem do staff (pública ou nota interna). Na 1ª resposta pública,
        marca `respondido_em` (conformidade do SLA de resposta)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna)
                VALUES ($1::uuid, $2::uuid, $3, $4)
                RETURNING id, created_at
                """,
                chamado_id,
                claims["sub"],
                conteudo,
                is_interna,
            )
            if not is_interna:
                await conn.execute(
                    "UPDATE chamados SET respondido_em = now() WHERE id = $1::uuid AND respondido_em IS NULL",
                    chamado_id,
                )
            return dict(row) if row else None


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

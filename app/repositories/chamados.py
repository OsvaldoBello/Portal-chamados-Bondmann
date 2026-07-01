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
                       c.cliente_id, c.created_at, c.limite_resposta, c.limite_resolucao,
                       c.resolvido_em, c.avaliacao_nota, c.avaliacao_comentario,
                       c.avaliacao_em, cat.nome AS categoria, dep.nome AS departamento,
                       autor.nome AS cliente_nome
                  FROM chamados c
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
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

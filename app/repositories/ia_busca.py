"""Busca de chamados semelhantes para a triagem por IA (F3 — plano IA, Seção 5).

Fase A: FTS português (migration ``0053``) sobre chamados ``RESOLVIDO`` do
**mesmo departamento** do chamado em triagem. Os termos vêm do próprio modelo
(``SaidaTriagem.termos_busca``); o resultado (código + título + resolução
registrada) é citado na nota interna. Fase B (pgvector) fica para quando o
volume justificar (Seção 4.5).

Campos pesquisados: o casamento e o ranqueamento cobrem **título + descrição**
(coluna gerada ``chamados.fts``) **e a resolução registrada** — esta última
concatenada ao ``tsvector`` em tempo de busca (``c.fts || to_tsvector(res)``),
sem coluna materializada nova. Assim um chamado passado é encontrado tanto pelo
sintoma (título/descrição) quanto pela solução aplicada. A "resolução
registrada" é a última mensagem pública de staff do chamado resolvido
(Seção 4.4 — não há coluna de resolução); pesquisa e exibição usam exatamente o
mesmo texto, então nunca se cita uma resolução que não casou. Como o predicado
passa a incidir sobre uma expressão (não só sobre ``c.fts``), o índice GIN
``idx_chamados_fts`` deixa de filtrar sozinho; os filtros duros
(``departamento_id`` + ``status='RESOLVIDO'``) mantêm o conjunto candidato
pequeno na v1 — se o volume justificar, promover a resolução a coluna
materializada indexada (mesma decisão de engenharia da Seção 4.4).

Contrato de segurança (Seção 5): a consulta roda na conexão administrativa do
motor, então o filtro por ``departamento_id`` é **obrigatório no SQL** — defesa
em profundidade, mesmo padrão do plano mestre geral.

Este módulo recebe a conexão de quem chama (o motor já tem a administrativa
aberta na fase de persistência) — não abre conexão própria.
"""

from __future__ import annotations

from typing import Any

MAX_SEMELHANTES = 3

_SQL_SEMELHANTES = """
SELECT c.id::text AS id, c.codigo, c.titulo, res.conteudo AS resolucao
  FROM chamados c
  LEFT JOIN LATERAL (
        SELECT m.conteudo
          FROM mensagens m
         WHERE m.chamado_id = c.id
           AND m.is_interna = false
           AND m.remetente_id <> c.cliente_id
           AND m.conteudo <> ''
         ORDER BY m.created_at DESC
         LIMIT 1
       ) res ON true
 WHERE c.departamento_id = $1
   AND c.id <> $2::uuid
   AND c.status = 'RESOLVIDO'
   AND (c.fts || to_tsvector('portuguese', coalesce(res.conteudo, '')))
       @@ websearch_to_tsquery('portuguese', $3)
 ORDER BY ts_rank(c.fts || to_tsvector('portuguese', coalesce(res.conteudo, '')),
                  websearch_to_tsquery('portuguese', $3)) DESC,
          c.resolvido_em DESC NULLS LAST
 LIMIT $4
"""


def montar_consulta(termos: list[str]) -> str:
    """``["a", "b"]`` → ``"a OR b"`` (união, não interseção — chamados parecidos
    raramente têm TODOS os termos). ``websearch_to_tsquery`` interpreta o OR e
    nunca levanta erro de sintaxe com entrada arbitrária do modelo."""
    return " OR ".join(t.strip() for t in termos if t and t.strip())


async def buscar_semelhantes(
    conn: Any,
    *,
    departamento_id: Any,
    chamado_id: str,
    termos: list[str],
    limite: int = MAX_SEMELHANTES,
) -> list[dict[str, Any]]:
    """Top-``limite`` chamados resolvidos do departamento que casam com os termos.

    Sem termos utilizáveis devolve ``[]`` sem tocar no banco (a nota degrada
    graciosamente — a seção de semelhantes é omitida, nunca inventada)."""
    consulta = montar_consulta(termos)
    if not consulta:
        return []
    rows = await conn.fetch(_SQL_SEMELHANTES, departamento_id, chamado_id, consulta, limite)
    return [dict(r) for r in rows]

"""Repositório de fila/kanban/operadores (Seção 3.1/5.1).

Extraído de `ChamadosRepo` (Sprint 2 / item 2.1, M1). Mesma regra das demais
partes do domínio: cada método abre uma transação curta via
:func:`rls_connection`, RLS impõe autorização/isolamento.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.db import rls_connection


class FilaRepo:
    """Fila/Kanban do operador (Fase 4) — escopo por departamento via RLS.

    🔁 Fase 1 (2026-07-09): a fila/kanban do setor deixou de misturar três
    coisas diferentes. Três recortes explícitos, sem depender só da RLS:
      - fila()/kanban (a atender): chamados do MEU setor abertos por quem
        NÃO é colega do mesmo departamento (nem eu mesmo) — pedido "de fora".
      - listar() ("Meus chamados", na fachada `ChamadosRepo`): os que EU
        mesmo abri, pra qualquer setor.
      - chamados_departamento() (novo): chamados abertos por OUTRO colega do
        MEU setor de origem, para QUALQUER departamento de destino (mesmo
        recorte de leitura da RLS de líder de setor, migration 0028).

    🔁 Combinação de chamados (migration 0065): **nenhum** dos recortes acima
    devolve duplicados (``chamado_principal_id IS NOT NULL``). Eles saíram do
    quadro no momento em que foram combinados — o atendimento acontece no
    chamado principal, e contá-los na fila/nos cartões seria o mesmo problema
    de volume inflado que a feature existe para resolver. O duplicado continua
    acessível pelo link direto (``/workspace/chamados/{id}``) e em "Meus
    chamados" do autor, com o aviso de para onde ele foi.
    """

    _FILA_COLUNAS = """
        SELECT c.id, c.codigo, c.titulo, c.status, c.prioridade, c.setor,
               c.created_at, c.data_entrega, c.sem_prazo,
               c.limite_resolucao, c.respondido_em, c.resolvido_em,
               c.departamento_id,
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
        setor: str | None = None,
        data_de: date | None = None,
        data_ate: date | None = None,
        busca: str | None = None,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        """Fila/Kanban (a atender): chamados do MEU setor abertos por alguém que
        NÃO é colega do mesmo departamento nem eu mesmo — pedidos "de fora" do
        setor (o público que a fila existe para atender). Chamados abertos por um
        colega ficam em :meth:`chamados_departamento`; os meus, em
        ``ChamadosRepo.listar``.

        **Departamentos com autoatendimento** (``departamentos.autoatendimento`` —
        todos os setores desde a migration 0047, generalizado a partir da exceção
        original de Marketing e RH, migrations 0038/0042): o setor não funciona só
        como suporte (alguém de fora abre, alguém do setor atende) — também é um
        quadro estilo Trello onde o próprio time cria e gerencia demandas (ex.: a
        diretoria abre um chamado pro TI sem precisar entrar na plataforma como
        staff). Por isso, pra chamados destinados a um departamento com a flag, o
        recorte "de fora do setor" não se aplica: toda demanda do departamento
        aparece aqui (kanban/fila), inclusive as próprias e as de colega, além de
        continuar em ``ChamadosRepo.listar`` ("Meus chamados").

        Filtros opcionais: ``status``, ``categoria_id``, ``prioridade``,
        ``operador_id``, ``setor`` (setor solicitante, texto livre), ``busca``
        (texto livre casado contra **assunto/título e descrição**, case-insensitive)
        e o período ``data_de``/``data_ate`` (sobre `created_at`, inclusive nas duas
        pontas — o filtro de SLA é aplicado na camada de rota, pois depende do
        cálculo de estado do domínio). **Ordenação padrão: data de entrega (mais
        próxima primeiro); sem data de entrega fica por último, por data de
        abertura (mais recentes primeiro).**
        """
        busca_norm = f"%{busca.strip()}%" if busca and busca.strip() else None
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                self._FILA_COLUNAS
                + """
                 WHERE ($6::uuid IS NULL OR c.departamento_id = $6::uuid)
                   AND c.chamado_principal_id IS NULL
                   AND (
                     dep.autoatendimento
                     OR (c.cliente_id <> auth.uid() AND autor.departamento_id IS DISTINCT FROM c.departamento_id)
                   )
                   AND ($1::status_chamado IS NULL OR c.status = $1::status_chamado)
                   AND ($3::uuid IS NULL OR c.categoria_id = $3::uuid)
                   AND ($4::prioridade_chamado IS NULL OR c.prioridade = $4::prioridade_chamado)
                   AND ($5::uuid IS NULL OR c.operador_id = $5::uuid)
                   AND ($7::text IS NULL OR c.setor = $7::text)
                   AND ($8::date IS NULL OR c.created_at >= $8::date)
                   AND ($9::date IS NULL OR c.created_at < ($9::date + 1))
                   AND ($10::text IS NULL OR c.titulo ILIKE $10 OR c.descricao ILIKE $10)
                 ORDER BY c.data_entrega ASC NULLS LAST, c.created_at DESC
                 LIMIT $2
                """,
                status,
                limite,
                categoria_id,
                prioridade,
                operador_id,
                departamento_id,
                setor,
                data_de,
                data_ate,
                busca_norm,
            )
            return [dict(r) for r in rows]

    async def setores_ativos(self, claims: dict, departamento_id: str | None = None) -> list[str]:
        """Valores distintos de `setor` (setor solicitante) já usados em chamados
        do departamento — alimenta o select de filtro do Kanban/fila."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT DISTINCT c.setor FROM chamados c
                    WHERE c.setor IS NOT NULL AND c.setor <> ''
                      AND ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                    ORDER BY c.setor""",
                departamento_id,
            )
            return [r["setor"] for r in rows]

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
                   AND c.chamado_principal_id IS NULL
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
        (chamados "de fora" do setor, exceto autoatendimento — ver docstring de
        `fila`); os demais filtros entram no ETag pela rota."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """SELECT count(*)::int AS n, max(c.updated_at) AS mx
                     FROM chamados c
                     LEFT JOIN perfis autor ON autor.id = c.cliente_id
                     LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                    WHERE ($2::uuid IS NULL OR c.departamento_id = $2::uuid)
                      AND c.chamado_principal_id IS NULL
                      AND (
                        dep.autoatendimento
                        OR (c.cliente_id <> auth.uid() AND autor.departamento_id IS DISTINCT FROM c.departamento_id)
                      )
                      AND ($1::status_chamado IS NULL OR c.status = $1::status_chamado)""",
                status,
                departamento_id,
            )
            return (row["n"], row["mx"])

    async def fila_stats(self, claims: dict, *, departamento_id: str | None = None) -> dict[str, int]:
        """Contagem por status no escopo da fila/kanban (cabeçalhos), mesmo
        recorte de :meth:`fila` — não conta chamados próprios nem de colegas,
        exceto nos departamentos com autoatendimento (todos, desde a 0047;
        quadro Trello: toda demanda do setor conta)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT c.status, count(*) AS n
                     FROM chamados c
                     LEFT JOIN perfis autor ON autor.id = c.cliente_id
                     LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                    WHERE ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                      AND c.chamado_principal_id IS NULL
                      AND (
                        dep.autoatendimento
                        OR (c.cliente_id <> auth.uid() AND autor.departamento_id IS DISTINCT FROM c.departamento_id)
                      )
                    GROUP BY c.status""",
                departamento_id,
            )
        por = {r["status"]: r["n"] for r in rows}
        return {
            "total": sum(por.values()),
            "NOVO": por.get("NOVO", 0),
            "A_FAZER": por.get("A_FAZER", 0),
            "PROJETOS": por.get("PROJETOS", 0),
            "EM_ATENDIMENTO": por.get("EM_ATENDIMENTO", 0),
            "RESPOSTA_CLIENTE": por.get("RESPOSTA_CLIENTE", 0),
            "AGUARDANDO_TERCEIROS": por.get("AGUARDANDO_TERCEIROS", 0),
            "AGUARDANDO": por.get("AGUARDANDO", 0),
            "RESOLVIDO": por.get("RESOLVIDO", 0),
        }

    async def candidatos_combinacao(
        self, claims: dict, chamado_id: str, *, busca: str | None = None, limite: int = 10
    ) -> list[dict[str, Any]]:
        """Chamados que podem ser combinados NESTE (migration 0065).

        Elegível = mesmo departamento de destino, ainda em aberto, que não seja
        duplicado nem principal de ninguém (o trigger recusaria os dois casos —
        aqui a lista já nasce sem eles, para não oferecer o que vai falhar).

        Ordenação **por semelhança com o chamado atual**, e é isso que faz a
        tela servir ao caso real: quando o servidor cai e chegam 8 chamados
        parecidos, os candidatos certos vêm no topo sem ninguém digitar nada. O
        ranking usa a coluna FTS em português (``chamados.fts``, migration 0053,
        já usada pela busca de semelhantes da IA) contra o título deste chamado;
        como é ORDER BY e não WHERE, um casamento fraco não some da lista —
        apenas desce. ``busca`` (código/assunto/descrição) filtra por cima,
        para quem já sabe o número do chamado repetido.
        """
        busca_norm = f"%{busca.strip()}%" if busca and busca.strip() else None
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status, c.created_at,
                       autor.nome AS cliente_nome
                  FROM chamados c
                  JOIN chamados alvo ON alvo.id = $1::uuid
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                 WHERE c.id <> alvo.id
                   AND c.departamento_id = alvo.departamento_id
                   AND c.chamado_principal_id IS NULL
                   AND c.status <> 'RESOLVIDO'::status_chamado
                   AND NOT EXISTS (
                     SELECT 1 FROM chamados f WHERE f.chamado_principal_id = c.id
                   )
                   AND ($2::text IS NULL
                        OR c.codigo ILIKE $2 OR c.titulo ILIKE $2 OR c.descricao ILIKE $2)
                 ORDER BY ts_rank(c.fts, plainto_tsquery('portuguese', alvo.titulo)) DESC,
                          c.created_at DESC
                 LIMIT $3
                """,
                chamado_id,
                busca_norm,
                limite,
            )
            return [dict(r) for r in rows]

    async def operadores(
        self, claims: dict, *, departamento_id: str | None = None, excluir_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Staff disponível para atribuição (role OPERADOR/ADMIN).

        Quando ``departamento_id`` é informado, retorna **apenas** o staff daquele
        setor — a atribuição de responsável é sempre dentro do departamento do
        chamado (RH atribui RH; TI, ao atender um chamado de RH, atribui RH).
        A troca de setor é uma ação separada (só TI — ver
        ``AtendimentoRepo.transferir``). ``excluir_id`` tira um usuário
        específico da lista — usado para excluir o autor do chamado (autor
        nunca é o próprio responsável)."""
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

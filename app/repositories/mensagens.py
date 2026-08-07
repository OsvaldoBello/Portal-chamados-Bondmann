"""Repositório de mensagens/notificações/observadores (Seção 3.1/5.1).

Extraído de `ChamadosRepo` (Sprint 2 / item 2.1, M1). Mesma regra das demais
partes do domínio: cada método abre uma transação curta via
:func:`rls_connection`, RLS impõe autorização/isolamento.
"""

from __future__ import annotations

import json
from typing import Any

from app.db import rls_connection


class MensagensRepo:
    """Mensagens (públicas/nota interna), notificações e observadores em cópia."""

    async def mensagens_assinatura(self, claims: dict, chamado_id: str) -> tuple[int, Any]:
        """Assinatura leve da conversa (count + max ``created_at``) no escopo do
        chamado — RLS aplica o mesmo recorte de :meth:`mensagens` (nota interna
        não entra pro autor). Usada para ETag/304 no polling do chat (Seção 2.2):
        se nada mudou, evita buscar/re-renderizar as mensagens e — principal causa
        do "piscar" da conversa — regenerar as signed URLs dos anexos a cada 10s
        sem necessidade (a URL muda a cada render mesmo quando o conteúdo é o
        mesmo, forçando o navegador a recarregar as imagens)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                "SELECT count(*)::int AS n, max(created_at) AS mx FROM mensagens WHERE chamado_id = $1::uuid",
                chamado_id,
            )
            return (row["n"], row["mx"])

    async def mensagens(self, claims: dict, chamado_id: str) -> list[dict[str, Any]]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT m.id, m.conteudo, m.is_interna, m.created_at, m.anexos,
                       p.nome AS remetente_nome, p.role AS remetente_role,
                       p.avatar_path AS remetente_avatar_path,
                       p.updated_at AS remetente_avatar_atualizado_em
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
        Serve tanto ao sino do staff (fila do setor) quanto ao do funcionário.

        Chamado aberto para o PRÓPRIO departamento do autor (``chamados.
        departamento_id = perfis.departamento_id``) fica de fora do segundo
        recorte: ali o setor se autoatende num quadro estilo Trello (ex.:
        Marketing pedindo pro Marketing), sem uma relação real de "quem
        prestou o serviço" — a trava de avaliação só faz sentido pra quem
        pediu algo a OUTRO departamento (2026-07-23). Sem essa exclusão, o
        chamado ficava pendente pra sempre e o sino nunca apagava a bolinha de
        aviso. Antes disso a regra comparava ``operador_id`` com
        ``cliente_id``, mas isso falhava sempre que o card era resolvido por
        um colega do mesmo setor.

        Duplicado de combinação (``chamado_principal_id``, migration 0065) não
        entra em nenhum dos dois recortes: quem precisa de atenção é o chamado
        principal, e o autor do duplicado já está em cópia nele — o sino
        apontaria duas vezes para o mesmo trabalho.

        ``nao_visto`` (2026-07-23): a lista em si continua mostrando TODO
        pendente, mas a bolinha do sino (``_notificacoes.html``, que só olha
        NOVO/RESOLVIDO) precisa saber se o usuário já ABRIU aquele chamado
        desde a última mudança — sem isso um chamado NOVO já atribuído a
        alguém, mas sem "Iniciar atendimento" clicado, mantinha a bolinha
        acesa pra sempre mesmo depois de conferido. Comparamos contra
        ``chamados_notificacoes_vistas.chamado_updated_em`` (o ``updated_at``
        do chamado no momento em que foi visto) em vez de um timestamp de
        parede: dispensa relógio de cliente e volta a acender sozinho se o
        chamado mudar de novo (reatribuição, nova mensagem, reabertura)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status,
                       c.created_at, c.limite_resolucao, c.resolvido_em,
                       c.avaliacao_nota, (c.cliente_id = auth.uid()) AS meu,
                       (v.chamado_updated_em IS NULL OR v.chamado_updated_em < c.updated_at) AS nao_visto
                  FROM chamados c
                  LEFT JOIN perfis cli ON cli.id = c.cliente_id
                  LEFT JOIN chamados_notificacoes_vistas v
                         ON v.chamado_id = c.id AND v.perfil_id = auth.uid()
                 WHERE c.chamado_principal_id IS NULL
                   AND (
                     c.status <> 'RESOLVIDO'
                     OR (c.resolvido_em IS NOT NULL AND c.avaliacao_nota IS NULL
                         AND c.cliente_id = auth.uid()
                         AND c.departamento_id IS DISTINCT FROM cli.departamento_id)
                   )
                 ORDER BY COALESCE(c.updated_at, c.created_at) DESC
                 LIMIT $1
                """,
                limite,
            )
            return [dict(r) for r in rows]

    async def marcar_notificacao_vista(self, claims: dict, chamado_id: str) -> None:
        """Registra que o usuário abriu o chamado AGORA — chamado ao carregar a
        tela de detalhe (portal e workspace). Grava o ``updated_at`` atual do
        chamado como referência (ver :meth:`notificacoes`); silencioso se o
        chamado não existe/não está no escopo do usuário (RLS), pois marcar
        "visto" nunca deve derrubar o carregamento da página."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                """
                INSERT INTO chamados_notificacoes_vistas (chamado_id, perfil_id, chamado_updated_em, visto_em)
                SELECT c.id, auth.uid(), c.updated_at, now()
                  FROM chamados c
                 WHERE c.id = $1::uuid
                ON CONFLICT (chamado_id, perfil_id)
                DO UPDATE SET chamado_updated_em = EXCLUDED.chamado_updated_em,
                               visto_em = EXCLUDED.visto_em
                """,
                chamado_id,
            )

    # ---------------------------------------------------------------------
    # "Em cópia" — observadores multi-setoriais (Fase 8, 2026-07-09).
    # ---------------------------------------------------------------------
    async def usuarios_para_copia(
        self, claims: dict, *, excluir_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Usuários ativos de QUALQUER setor, para o seletor "Adicionar em
        cópia" — diferente de :meth:`FilaRepo.operadores` (só staff do setor do
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

    async def chamados_em_copia(
        self, claims: dict, *, limite: int = 100
    ) -> list[dict[str, Any]]:
        """Chamados em que o usuário está "em cópia" (observador) — mas NÃO é o
        autor. Sem isso, quem é adicionado como observador só enxerga o
        chamado pelo link direto (sino/e-mail): a RLS (`chamados_select`,
        migration 0034/0036) já libera a leitura, mas `ChamadosRepo.listar`
        ("Meus chamados") filtra por `cliente_id = auth.uid()` e nunca devolve
        essas linhas — o motivo do bug relatado pelo RH (2026-08-07): cópia
        "invisível" na listagem/kanban de quem foi adicionado.

        Exclui duplicados (`chamado_principal_id IS NOT NULL`) pelo mesmo
        motivo da fila/`ChamadosRepo.listar`: quem combinou já vira observador
        do principal, então o duplicado em si não precisa duplicar a linha
        aqui — a informação relevante está no principal."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status, c.prioridade, c.created_at,
                       cat.nome AS categoria, dep.nome AS departamento,
                       autor.nome AS cliente_nome
                  FROM chamados_observadores o
                  JOIN chamados c ON c.id = o.chamado_id
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                 WHERE o.perfil_id = $1::uuid
                   AND c.cliente_id <> $1::uuid
                   AND c.chamado_principal_id IS NULL
                 ORDER BY c.created_at DESC
                 LIMIT $2
                """,
                claims["sub"],
                limite,
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

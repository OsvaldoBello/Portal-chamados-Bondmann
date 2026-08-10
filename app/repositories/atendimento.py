"""Repositório de atendimento — ciclo de vida do chamado (Seção 3.1/5.1).

Extraído de `ChamadosRepo` (Sprint 2 / item 2.1, M1). Mesma regra das demais
partes do domínio: cada método abre uma transação curta via
:func:`rls_connection`, RLS impõe autorização/isolamento.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from asyncpg import PostgresError

from app.db import admin_connection, rls_connection
from app.domain.combinacao import anexos_combinacao, texto_combinacao
from app.domain.formularios_rh import FormularioObrigatorio, formulario_da_subcategoria

log = logging.getLogger("app.repositories.atendimento")

# Whitelist completa do enum `status_chamado` (o que cada setor OFERECE na UI é
# um subconjunto — ver `app/routes/workspace.py::_status_ui`). Vive aqui, junto
# de quem escreve status no banco, e é reexportada pela fachada `ChamadosRepo`.
STATUS_CHAMADO = (
    "NOVO", "A_FAZER", "PROJETOS", "EM_ATENDIMENTO", "RESPOSTA_CLIENTE",
    "AGUARDANDO_TERCEIROS", "AGUARDANDO", "RESOLVIDO",
)

# Status para o qual um duplicado volta ao desfazer a combinação, quando o
# histórico não guarda de onde ele veio (registro apagado/anterior à 0065).
STATUS_PADRAO_DESCOMBINAR = "EM_ATENDIMENTO"


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
                       c.sem_prazo, c.categoria_id, c.subcategoria_id, c.telefone_contato,
                       c.created_at, c.limite_resposta, c.limite_resolucao,
                       c.respondido_em, c.resolvido_em,
                       c.avaliacao_nota, c.avaliacao_comentario, c.avaliacao_em,
                       c.volume, c.origem_demanda, c.causa_atraso,
                       c.dados_formulario, c.resumo_ia, c.resumo_ia_em,
                       c.chamado_principal_id, c.combinado_em,
                       c.prazo_projeto_dias, c.projeto_em,
                       cat.nome AS categoria, sub.nome AS subcategoria,
                       dep.nome AS departamento, dep.autoatendimento,
                       autor.nome AS cliente_nome, autor.avatar_path AS cliente_avatar_path,
                       autor.updated_at AS cliente_avatar_atualizado_em,
                       autor.departamento_id AS cliente_departamento_id,
                       op.nome AS operador_nome,
                       princ.codigo AS principal_codigo
                  FROM chamados c
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN subcategorias sub ON sub.id = c.subcategoria_id
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                  LEFT JOIN perfis op ON op.id = c.operador_id
                  -- Combinação (0065): o código do principal alimenta o aviso
                  -- "combinado com BOND-…" no Portal e no Workspace. Quem não
                  -- enxerga o principal (RLS) recebe NULL aqui — mas o autor de
                  -- um duplicado sempre enxerga, pois entra em cópia no principal.
                  LEFT JOIN chamados princ ON princ.id = c.chamado_principal_id
                 WHERE c.id = $1::uuid
                """,
                chamado_id,
            )
            if row is None:
                return None
            d = dict(row)
            # asyncpg devolve jsonb como texto; normaliza para dict de respostas.
            bruto = d.get("dados_formulario")
            d["dados_formulario"] = json.loads(bruto) if isinstance(bruto, str) else (bruto or {})
            return d

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
        telefone_contato: str,
        data_entrega: date | None = None,
        volume: int = 1,
        origem_demanda: str = "Solicitação",
        sem_prazo: bool = False,
        dados_formulario: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Cria um chamado endereçado a um departamento. Código/SLA via triggers.

        ``data_entrega`` (fluxo por demanda do Marketing) define o prazo de SLA
        diretamente — o trigger ``calcular_sla_chamado`` usa a data em vez da
        prioridade quando ela é informada (migration 0022). ``sem_prazo`` (0040)
        é o oposto: demanda sem urgência nem prazo, o trigger não calcula SLA
        nenhum (tem prioridade sobre ``data_entrega``).

        ``dados_formulario`` (0049) guarda as respostas dos campos dinâmicos por
        categoria (ex.: Químico) como objeto ``{name: valor}``; ``{}`` para
        chamados sem layout específico. ``telefone_contato`` (0058) é obrigatório
        na abertura — já validado (``validar_telefone_contato``) antes de chegar
        aqui."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO chamados
                    (empresa_id, cliente_id, categoria_id, subcategoria_id, departamento_id,
                     titulo, descricao, prioridade, data_entrega, setor, volume, origem_demanda,
                     sem_prazo, dados_formulario, telefone_contato)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5::uuid, $6, $7,
                        $8::prioridade_chamado, $9::date, $10, $11::integer, $12, $13::boolean,
                        $14::jsonb, $15)
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
                json.dumps(dados_formulario or {}),
                telefone_contato,
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

    async def reabrir(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        """Reabre um chamado RESOLVIDO por iniciativa do autor, insatisfeito com
        a solução (0059): volta para EM_ATENDIMENTO com o mesmo operador (RLS +
        trigger ``enforce_cliente_so_avaliacao`` só liberam essa transição
        específica para o CLIENTE), limpa ``resolvido_em`` e zera uma eventual
        avaliação anterior — a nota antiga não se aplica mais à nova rodada de
        atendimento; reabre o chat (``chamado_detalhe.html`` esconde o composer
        só quando ``status == 'RESOLVIDO'``)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET status = 'EM_ATENDIMENTO'::status_chamado,
                       resolvido_em = NULL,
                       avaliacao_nota = NULL,
                       avaliacao_comentario = NULL,
                       avaliacao_em = NULL
                 WHERE id = $1::uuid AND status = 'RESOLVIDO'::status_chamado
             RETURNING id, status
                """,
                chamado_id,
            )
            if row is None:
                return None
            await self._registrar(conn, chamado_id, claims["sub"], "REABERTO", {})
            return dict(row)

    async def avaliacao_pendente(self, claims: dict) -> dict[str, Any] | None:
        """O chamado RESOLVIDO mais antigo do próprio autor ainda sem avaliação
        (1-5 ★), se houver — usado para redirecionar quem tenta abrir um novo
        chamado antes de avaliar o anterior (2026-07-21). Mesmo recorte de
        ``MensagensRepo.notificacoes`` (resolvido + sem nota + próprio autor),
        aqui restrito a um único registro e explicitamente filtrado por
        ``cliente_id`` (a rota que consome isto é aberta a qualquer papel
        autenticado, não só CLIENTE — RLS por si só não estreita o bastante).

        Chamado aberto para o PRÓPRIO departamento do autor (``chamados.
        departamento_id = perfis.departamento_id``, ex.: alguém do Marketing
        pedindo pro Marketing) não entra: ali o setor se autoatende num quadro
        estilo Trello, sem uma relação real de "quem prestou o serviço" — a
        trava de avaliação só faz sentido pra quem pediu algo a OUTRO
        departamento (2026-07-23, ex.: BOND-2026-00027 — recorrente no
        Marketing, travava a abertura de um novo chamado mesmo sem ninguém
        pra avaliar). Antes disso a regra comparava ``operador_id`` com
        ``cliente_id``, mas isso falhava sempre que o card era resolvido por
        um colega do mesmo setor (ou arrastado direto até "Resolvido" sem
        nunca ser reivindicado, deixando ``operador_id`` nulo).

        Duplicado de uma combinação (0065) também fica de fora: ele é encerrado
        como RESOLVIDO sem ninguém ter atendido *aquele* chamado, então travar a
        abertura de um novo chamado pedindo nota nele seria pedir CSAT de um
        atendimento que acontece no principal (onde o autor está em cópia)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                SELECT c.id, c.codigo, c.titulo
                  FROM chamados c
                  JOIN perfis cli ON cli.id = c.cliente_id
                 WHERE c.cliente_id = $1::uuid
                   AND c.status = 'RESOLVIDO'
                   AND c.avaliacao_nota IS NULL
                   AND c.chamado_principal_id IS NULL
                   AND c.departamento_id IS DISTINCT FROM cli.departamento_id
                 ORDER BY c.resolvido_em ASC NULLS LAST
                 LIMIT 1
                """,
                claims["sub"],
            )
            return dict(row) if row else None

    async def ia_triagem_nota(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        """Última triagem por IA que gerou nota interna no chamado, com a
        avaliação atual do staff (1–5 ★), se houver.

        Roda sob RLS: a policy `ia_triagens_select_staff` (0050) já restringe a
        leitura ao staff do departamento do chamado — quem não é do escopo
        recebe `None` e o bloco de avaliação nem aparece na tela."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                SELECT t.id, t.rodada, t.modelo, t.created_at,
                       t.avaliacao, t.avaliado_em, p.nome AS avaliado_por_nome
                  FROM ia_triagens t
                  LEFT JOIN perfis p ON p.id = t.avaliado_por
                 WHERE t.chamado_id = $1::uuid AND t.acao = 'NOTA_INTERNA'
                 ORDER BY t.rodada DESC, t.id DESC
                 LIMIT 1
                """,
                chamado_id,
            )
            return dict(row) if row else None

    async def avaliar_ia_triagem(
        self, claims: dict, chamado_id: str, *, triagem_id: int, nota: int, avaliador_id: str
    ) -> bool:
        """Avaliação 1–5 ★ da pré-análise da IA pelo staff (KPI Seção 10.2).

        `ia_triagens` não tem policy de escrita (decisão da 0050): o escopo é
        provado ANTES, lendo a triagem sob RLS com os claims do avaliador — se
        ele enxerga a linha, é staff do departamento do chamado. Só então a
        escrita acontece pela conexão administrativa. Reavaliar sobrescreve
        (vale a última opinião do time)."""
        async with rls_connection(claims) as conn:
            visivel = await conn.fetchval(
                """
                SELECT 1 FROM ia_triagens
                 WHERE id = $1 AND chamado_id = $2::uuid AND acao = 'NOTA_INTERNA'
                """,
                triagem_id,
                chamado_id,
            )
        if not visivel:
            return False
        async with admin_connection() as conn:
            await conn.execute(
                """
                UPDATE ia_triagens
                   SET avaliacao = $2, avaliado_por = $3::uuid, avaliado_em = now()
                 WHERE id = $1
                """,
                triagem_id,
                nota,
                avaliador_id,
            )
        return True

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
        autoatendimento** (``departamentos.autoatendimento`` — todos os setores desde
        a migration 0047, generalizado a partir da exceção original de Marketing/RH,
        migrations 0038/0042), onde o próprio setor cria e gerencia as demandas
        (quadro estilo Trello). Registra ``ATENDIMENTO_INICIADO`` no histórico.
        Escopo por RLS (staff)."""
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

    # ---------------------------------------------------------------------
    # Combinação de chamados duplicados (migration 0065)
    # ---------------------------------------------------------------------
    async def combinados(self, claims: dict, chamado_id: str) -> list[dict[str, Any]]:
        """Chamados combinados NESTE (os duplicados que ele absorveu)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.combinado_em,
                       autor.nome AS cliente_nome
                  FROM chamados c
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                 WHERE c.chamado_principal_id = $1::uuid
                 ORDER BY c.combinado_em ASC NULLS LAST
                """,
                chamado_id,
            )
            return [dict(r) for r in rows]

    async def combinar(
        self, claims: dict, *, principal_id: str, duplicado_id: str
    ) -> dict[str, Any]:
        """Combina ``duplicado_id`` no chamado ``principal_id`` (0065).

        Tudo **sob RLS** — nada de conexão administrativa: as três escritas
        (marcar o duplicado, mover a cópia, publicar a digest) já são permitidas
        ao staff do setor pelas policies existentes. Elas ficam num SAVEPOINT
        comum, então ou a combinação inteira vale, ou nenhuma parte dela vale —
        nunca um chamado marcado como duplicado sem ninguém em cópia.

        O que acontece com o duplicado: ganha ``chamado_principal_id`` e é
        encerrado como ``RESOLVIDO`` (para o relógio de SLA e sai do quadro).
        Quem o exclui dos indicadores é a coluna, não o status — ver a migration.

        O que acontece com o principal: recebe **uma** mensagem pública com o
        DESCRITIVO do duplicado (`app/domain/combinacao.py` — assunto, descrição
        e anexos; a conversa do duplicado não é copiada, ver o módulo) e passa a
        ter, em cópia (`chamados_observadores`, 0034), o autor do duplicado e
        quem já estava em cópia nele — é o que faz "todo mundo receber as
        atualizações em conjunto" sem nenhuma policy nova.

        Devolve ``{"ok", "codigo", "erro"}``: as recusas são de negócio (o
        usuário precisa entender o motivo), não exceções. O banco reforça as
        mesmas regras no trigger ``enforce_combinacao_chamados`` — o que é
        checado aqui é para dar a mensagem certa, não para valer como trava.
        """
        if str(principal_id) == str(duplicado_id):
            return {"ok": False, "codigo": None, "erro": "Um chamado não pode ser combinado com ele mesmo."}

        async with rls_connection(claims) as conn:
            principal = await conn.fetchrow(
                """
                SELECT c.id, c.codigo, c.cliente_id, c.operador_id, c.departamento_id,
                       c.chamado_principal_id, COALESCE(dep.autoatendimento, false) AS autoatendimento
                  FROM chamados c
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                 WHERE c.id = $1::uuid
                """,
                principal_id,
            )
            duplicado = await conn.fetchrow(
                """
                SELECT c.id, c.codigo, c.titulo, c.descricao, c.status, c.created_at,
                       c.cliente_id, c.telefone_contato, c.departamento_id, c.chamado_principal_id,
                       autor.nome AS cliente_nome, dep_autor.nome AS cliente_departamento
                  FROM chamados c
                  LEFT JOIN perfis autor ON autor.id = c.cliente_id
                  LEFT JOIN departamentos dep_autor ON dep_autor.id = autor.departamento_id
                 WHERE c.id = $1::uuid
                """,
                duplicado_id,
            )
            if principal is None or duplicado is None:
                return {"ok": False, "codigo": None,
                        "erro": "Chamado não encontrado ou fora do seu escopo de atendimento."}

            codigo = duplicado["codigo"]
            if duplicado["chamado_principal_id"] is not None:
                return {"ok": False, "codigo": codigo, "erro": f"{codigo} já está combinado com outro chamado."}
            if principal["chamado_principal_id"] is not None:
                return {"ok": False, "codigo": codigo,
                        "erro": "Este chamado é um duplicado — combine no chamado principal."}
            if str(duplicado["departamento_id"]) != str(principal["departamento_id"]):
                return {"ok": False, "codigo": codigo,
                        "erro": f"{codigo} é de outro departamento de destino e não pode ser combinado aqui."}
            if await conn.fetchval(
                "SELECT 1 FROM chamados WHERE chamado_principal_id = $1::uuid LIMIT 1", duplicado_id
            ):
                return {"ok": False, "codigo": codigo,
                        "erro": f"{codigo} já é o principal de outros chamados — desfaça aquelas combinações antes."}
            # A digest é uma mensagem pública do staff no principal, e a policy
            # `mensagens_insert` (0042) só a aceita em chamado já assumido e por
            # quem não seja o autor dele (fora do autoatendimento). Barrar aqui
            # dá uma instrução; deixar passar geraria erro de RLS no meio da
            # transação e um 500 sem explicação.
            if principal["operador_id"] is None:
                return {"ok": False, "codigo": codigo,
                        "erro": "Assuma este chamado (Iniciar atendimento) antes de combinar outros nele."}
            if (
                str(principal["cliente_id"]) == str(claims["sub"])
                and not principal["autoatendimento"]
            ):
                return {"ok": False, "codigo": codigo,
                        "erro": "Você abriu este chamado — outra pessoa do setor precisa fazer a combinação."}

            # Só os ANEXOS das mensagens públicas entram na digest (o texto da
            # conversa não é copiado — ver `app/domain/combinacao.py`). O filtro
            # `is_interna = false` continua valendo: anexo de nota interna do
            # duplicado não pode virar anexo de mensagem pública do principal.
            mensagens = [dict(r) for r in await conn.fetch(
                """
                SELECT m.anexos
                  FROM mensagens m
                 WHERE m.chamado_id = $1::uuid AND m.is_interna = false
                 ORDER BY m.created_at ASC
                """,
                duplicado_id,
            )]
            for m in mensagens:
                bruto = m.get("anexos")
                m["anexos"] = json.loads(bruto) if isinstance(bruto, str) else (bruto or [])

            # SAVEPOINT (`conn.transaction()` aninhado): as escritas abaixo
            # rodam dentro da transação do REQUEST, que é compartilhada por
            # todas as chamadas do repositório (`rls_request_scope`). Sem o
            # savepoint, um erro de banco aqui — uma corrida entre dois
            # operadores combinando ao mesmo tempo, que as checagens acima não
            # têm como pegar — abortaria a transação inteira e levaria junto as
            # combinações já feitas neste POST e o render da página.
            try:
                async with conn.transaction():
                    marcado = await conn.fetchrow(
                        """
                        UPDATE chamados
                           SET chamado_principal_id = $2::uuid,
                               status = 'RESOLVIDO'::status_chamado,
                               resolvido_em = COALESCE(resolvido_em, now())
                         WHERE id = $1::uuid AND chamado_principal_id IS NULL
                     RETURNING id, codigo
                        """,
                        duplicado_id,
                        principal_id,
                    )
                    if marcado is None:
                        # RLS permitiu LER o duplicado (ex.: líder de setor
                        # acompanhando a própria equipe, 0028) mas não ESCREVER.
                        return {"ok": False, "codigo": codigo,
                                "erro": f"Sem permissão para combinar {codigo} — só o setor de destino dele pode."}

                    # Em cópia: o autor do duplicado + quem já estava em cópia
                    # nele. O autor do PRINCIPAL fica de fora (já é o dono).
                    await conn.execute(
                        """
                        INSERT INTO chamados_observadores (chamado_id, perfil_id, criado_por)
                        SELECT $1::uuid, s.pid, $3::uuid
                          FROM (
                                SELECT $2::uuid AS pid
                                 UNION
                                SELECT o.perfil_id FROM chamados_observadores o
                                 WHERE o.chamado_id = $4::uuid
                               ) s
                         WHERE s.pid IS DISTINCT FROM $5::uuid
                        ON CONFLICT DO NOTHING
                        """,
                        principal_id,
                        duplicado["cliente_id"],
                        claims["sub"],
                        duplicado_id,
                        principal["cliente_id"],
                    )

                    await conn.execute(
                        """
                        INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos)
                        VALUES ($1::uuid, $2::uuid, $3, false, $4::jsonb)
                        """,
                        principal_id,
                        claims["sub"],
                        texto_combinacao(dict(duplicado)),
                        json.dumps(anexos_combinacao(mensagens)),
                    )

                    await self._registrar(
                        conn, duplicado_id, claims["sub"], "COMBINADO",
                        {
                            "principal_id": str(principal_id),
                            "principal_codigo": principal["codigo"],
                            # De onde ele veio, para o "desfazer" saber ao que voltar.
                            "status_anterior": duplicado["status"],
                        },
                    )
                    await self._registrar(
                        conn, principal_id, claims["sub"], "COMBINACAO_RECEBIDA",
                        {"duplicado_id": str(duplicado_id), "duplicado_codigo": codigo},
                    )
            except PostgresError:
                # A mensagem do banco não vai para a tela (pode carregar detalhe
                # de schema); o motivo real fica no log para investigação.
                log.exception("Falha ao combinar %s em %s", duplicado_id, principal_id)
                return {"ok": False, "codigo": codigo,
                        "erro": f"Não foi possível combinar {codigo}. Recarregue a página e tente de novo."}
            return {"ok": True, "codigo": codigo, "erro": None}

    async def desfazer_combinacao(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        """Devolve um duplicado à vida própria (0065): limpa o vínculo e restaura
        o status que ele tinha antes, lido do ``COMBINADO`` mais recente no
        histórico.

        Não desfaz os efeitos colaterais de propósito: a digest publicada no
        principal continua lá (é histórico do atendimento, não lixo) e quem
        entrou em cópia continua em cópia — tirar alguém que talvez tenha sido
        adicionado por outro motivo faria mais estrago do que deixar. Removê-los
        segue possível, um a um, em "Em cópia" no Portal.
        """
        async with rls_connection(claims) as conn:
            atual = await conn.fetchval(
                "SELECT chamado_principal_id FROM chamados WHERE id = $1::uuid", chamado_id
            )
            if atual is None:
                return None
            anterior = await conn.fetchval(
                """
                SELECT h.detalhes->>'status_anterior'
                  FROM historico_chamados h
                 WHERE h.chamado_id = $1::uuid AND h.acao = 'COMBINADO'
                 ORDER BY h.created_at DESC
                 LIMIT 1
                """,
                chamado_id,
            )
            status = anterior if anterior in STATUS_CHAMADO else STATUS_PADRAO_DESCOMBINAR
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET chamado_principal_id = NULL,
                       status = $2::status_chamado,
                       resolvido_em = CASE WHEN $2 = 'RESOLVIDO' THEN resolvido_em ELSE NULL END
                 WHERE id = $1::uuid AND chamado_principal_id IS NOT NULL
             RETURNING id, status
                """,
                chamado_id,
                status,
            )
            if row is None:
                return None
            await self._registrar(
                conn, chamado_id, claims["sub"], "COMBINACAO_DESFEITA",
                {"principal_id": str(atual), "status": status},
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

    async def formulario_pendente(
        self, claims: dict, chamado_id: str
    ) -> FormularioObrigatorio | None:
        """A subcategoria do chamado exige um formulário (`app/domain/formularios_rh.py`)
        e nenhum anexo foi enviado ainda? Retorna o formulário pendente (para
        mostrar o link de download/nome) ou ``None`` (nada exigido, ou já
        anexado — em qualquer mensagem, pública ou nota interna, de quem for).

        Usado tanto para exibir o aviso na tela (Portal e Workspace) quanto
        para bloquear a conclusão do chamado (`alterar_status`, chamado a
        partir de `app/routes/workspace.py::mudar_status`/`encerrar`)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                SELECT sub.nome AS subcategoria,
                       EXISTS(
                         SELECT 1 FROM mensagens m
                          WHERE m.chamado_id = c.id AND jsonb_array_length(m.anexos) > 0
                       ) AS tem_anexo
                  FROM chamados c
                  LEFT JOIN subcategorias sub ON sub.id = c.subcategoria_id
                 WHERE c.id = $1::uuid
                """,
                chamado_id,
            )
        if row is None or row["tem_anexo"]:
            return None
        return formulario_da_subcategoria(row["subcategoria"])

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

    async def definir_prazo_projeto(
        self, claims: dict, chamado_id: str, dias: int | None
    ) -> dict[str, Any] | None:
        """Define o prazo (em dias corridos) de um chamado da coluna "Projetos"
        (0066). ``None`` devolve o chamado ao padrão do plano.

        Quem escreve o ``limite_resolucao`` é o trigger ``sla_projetos_prazo``,
        não este método: o prazo é contado da ENTRADA na coluna
        (``chamados.projeto_em``), então trocar os dias de um projeto que começou
        há três semanas ajusta a data final sem recomeçar a contagem de hoje. Um
        chamado que ainda não está em PROJETOS aceita o valor e só o aplica ao
        entrar na coluna.

        A faixa aceita já foi validada na rota (``validar_prazo_projeto``) e é
        reforçada pela CHECK ``chamados_prazo_projeto_faixa`` no banco."""
        async with rls_connection(claims) as conn:
            atual = await conn.fetchval(
                "SELECT prazo_projeto_dias FROM chamados WHERE id = $1::uuid", chamado_id
            )
            row = await conn.fetchrow(
                """
                UPDATE chamados SET prazo_projeto_dias = $2::integer
                 WHERE id = $1::uuid
             RETURNING id, prazo_projeto_dias, projeto_em, limite_resolucao
                """,
                chamado_id,
                dias,
            )
            if row is None:
                return None
            if atual != dias:
                await self._registrar(
                    conn, chamado_id, claims["sub"], "PRAZO_PROJETO_ALTERADO",
                    {"de": atual, "para": dias, "limite_resolucao": str(row["limite_resolucao"])},
                )
            return dict(row)

    async def alterar_categoria(
        self, claims: dict, chamado_id: str, *,
        categoria_id: str | None, subcategoria_id: str | None,
    ) -> dict[str, Any] | None:
        """Altera categoria/subcategoria de um chamado já aberto (staff no
        escopo) + histórico. O par categoria/departamento e categoria/
        subcategoria já foi validado na rota (mesma regra de
        ``CatalogoRepo`` usada na abertura, Seção 5)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET categoria_id = $2::uuid, subcategoria_id = $3::uuid
                 WHERE id = $1::uuid
             RETURNING id, categoria_id, subcategoria_id
                """,
                chamado_id,
                categoria_id,
                subcategoria_id,
            )
            if row is None:
                return None
            await self._registrar(
                conn, chamado_id, claims["sub"], "CATEGORIA_ALTERADA",
                {"categoria_id": categoria_id, "subcategoria_id": subcategoria_id},
            )
            return dict(row)

    async def atribuir(
        self, claims: dict, chamado_id: str, operador_id: str | None
    ) -> dict[str, Any] | None:
        """Atribui (ou remove) o operador responsável + histórico.

        Segregação de função: não deixa atribuir o autor do chamado como o
        próprio responsável (devolve None nesse caso — a UI já não lista o
        autor entre os operadores, isto é defesa em profundidade) — **exceto
        nos departamentos com autoatendimento** (todos os setores desde a 0047),
        onde o autor É o dono da própria demanda no quadro."""
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

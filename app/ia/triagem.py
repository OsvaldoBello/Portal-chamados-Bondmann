"""Motor de triagem de chamados por IA (F1 sombra + F2 perguntas/re-triagem).

Fluxo (Seção 2.3 do plano IA): chamado criado num departamento com triagem
ativa → background task → **uma chamada com saída estruturada** (JSON validado
por :class:`app.ia.schemas.SaidaTriagem`; 1 retry em JSON inválido) → decisão:

- ``NOTA_INTERNA`` — pré-análise assinada pelo perfil "Assistente IA". Sai
  DIRETO quando a informação é suficiente (ou em modo sombra, ou na última
  rodada com as lacunas sinalizadas, ou com atendimento já iniciado).
- ``PERGUNTAS`` (F2, fora do modo sombra) — mensagem PÚBLICA ao autor +
  e-mail (Reply-To inbound). A nota interna NÃO sai nesta rodada (decisão do
  usuário 2026-07-23: o ciclo de perguntas vem primeiro; a nota fecha o ciclo
  quando a resposta chegar — rodada N+1 — ou no teto de rodadas, com lacunas).

A máquina de rodadas é **inteiramente derivada do banco** (``ia_triagens``),
o que torna o motor idempotente por construção:

- rodada N+1 só executa se a última triagem foi ``PERGUNTAS`` **e** existe
  mensagem pública do AUTOR posterior a ela (task duplicada não re-tria);
- rodada > ``IA_TRIAGEM_MAX_RODADAS`` é impossível (teto + UNIQUE do banco);
- guarda de atendimento (ajuste 2026-07-23): atendimento iniciado NÃO suprime
  a nota interna da rodada 1 (ela existe para quem atende) — força a ação a
  ``NOTA_INTERNA`` e veta re-triagem; a guarda é reavaliada com estado fresco
  após a chamada de modelo. Só chamado ``RESOLVIDO`` fica fora da triagem.

Garantias permanentes (Regras de Ouro): kill switch sem efeito colateral;
falha silenciosa ponta a ponta; a IA nunca conclui/altera nada (sugestões são
texto de nota); ``is_interna=true`` fixado em código na persistência da nota
(invariante 8.2 — o canal público tem função própria, usada só em PERGUNTAS).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.db import admin_connection
from app.domain.formularios_quimico import rotular
from app.ia import cliente
from app.ia.schemas import SaidaTriagem
from app.notification import notificar_nova_mensagem_email
from app.repositories import ia_busca

log = logging.getLogger("app.ia_triagem")

PERFIL_IA_NOME = "Assistente IA"
_PASSE_UNICO = "UNICO"
_MAX_TOKENS_SAIDA = 900

# Cache em memória do UUID do perfil de serviço (Seção 4.2 — lookup por nome,
# sem env extra, sem hardcode). O perfil nunca muda em runtime.
_perfil_ia_id: str | None = None


def deve_triar(departamento_nome: str | None, settings: Settings) -> bool:
    """Os hooks (abertura e resposta do autor) só agendam a task quando a
    triagem está ligada para o departamento (o motor revalida tudo ao rodar)."""
    return (
        settings.ia_triagem_ativa
        and bool(settings.ia_triagem_api_key)
        and (departamento_nome or "") in settings.ia_triagem_departamentos_lista
    )


@lru_cache(maxsize=8)
def _prompt(nome: str) -> str:
    """Prompt de sistema versionado em ``app/ia/prompts/<nome>.md`` (Regra #8).

    O cabeçalho de documentação do arquivo (antes do primeiro ``---``) não é
    enviado ao modelo."""
    texto = (Path(__file__).parent / "prompts" / f"{nome}.md").read_text(encoding="utf-8")
    return texto.split("\n---\n", 1)[1].strip()


def montar_mensagens(
    chamado: dict[str, Any],
    categorias: list[str],
    conversa: list[dict[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Mensagens system+user do passe único (função pura, testável).

    ``conversa`` (rodadas > 1): lista de ``{"papel": ..., "conteudo": ...}`` com
    a troca pública até aqui — perguntas da IA e respostas do autor."""
    linhas = [
        "## Chamado",
        f"Departamento: {chamado.get('departamento') or '—'}",
        f"Categoria escolhida: {chamado.get('categoria') or '—'}",
        f"Prioridade declarada: {chamado.get('prioridade') or '—'}",
        f"Assunto: {chamado.get('titulo') or '—'}",
        "",
        "Descrição:",
        str(chamado.get("descricao") or ""),
    ]
    pares = rotular(chamado.get("categoria"), chamado.get("dados_formulario") or {})
    if pares:
        linhas += ["", "Campos do formulário:"]
        linhas += [f"- {rotulo}: {valor}" for rotulo, valor in pares]
    if conversa:
        linhas += ["", "## Conversa pública até agora (rodada de re-triagem)"]
        linhas += [f"[{m['papel']}] {m['conteudo']}" for m in conversa]
        linhas += [
            "",
            "Considere as respostas do autor acima; não repita perguntas já respondidas.",
        ]
    if categorias:
        linhas += ["", "## Catálogo de categorias do departamento"]
        linhas += [f"- {nome}" for nome in categorias]
    return [
        {"role": "system", "content": _prompt("ti")},
        {"role": "user", "content": "\n".join(linhas)},
    ]


def decidir_acao(
    saida: SaidaTriagem,
    rodada: int,
    settings: Settings,
    atendimento_iniciado: bool = False,
) -> str:
    """Decide entre nota interna e perguntas públicas (Seção 2.3, função pura).

    Informação suficiente ⇒ nota DIRETO. Insuficiente ⇒ o ciclo de perguntas
    vem primeiro e a nota fica para o fim do ciclo (decisão do usuário
    2026-07-23). ``PERGUNTAS`` exige TODAS: ninguém atendendo ainda (com
    atendimento humano em curso, interpelar o autor seria ruído); fora do modo
    sombra; informação insuficiente; há perguntas; confiança ALTA (mitigação
    10.1 — não irritar usuário com pergunta especulativa); e ainda há rodada
    disponível (na última, a nota interna sai com as lacunas sinalizadas)."""
    if atendimento_iniciado:
        return "NOTA_INTERNA"
    if settings.ia_triagem_modo_sombra:
        return "NOTA_INTERNA"
    if saida.informacoes_suficientes or not saida.perguntas:
        return "NOTA_INTERNA"
    if saida.confianca != "ALTA":
        return "NOTA_INTERNA"
    if rodada >= settings.ia_triagem_max_rodadas:
        return "NOTA_INTERNA"
    return "PERGUNTAS"


def _resumir_resolucao(texto: str | None, max_chars: int = 280) -> str:
    """Resolução do chamado semelhante numa linha (espaços normalizados,
    truncada) — a nota cita a solução, não reproduz a conversa inteira."""
    limpo = " ".join((texto or "").split())
    if len(limpo) > max_chars:
        limpo = limpo[: max_chars - 1].rstrip() + "…"
    return limpo


def montar_nota(
    saida: SaidaTriagem,
    chamado: dict[str, Any],
    semelhantes: list[dict[str, Any]] | None = None,
) -> str:
    """Texto da nota interna a partir da saída estruturada (função pura).

    ``semelhantes`` (F3): chamados resolvidos citados como referência (código +
    resolução registrada). Lista vazia/None omite a seção — degradação graciosa
    (DoD F3: seção omitida, nunca inventada)."""
    partes = ["Triagem automática — pré-análise da IA", "", saida.pre_analise.strip()]

    sugestoes = []
    if saida.categoria_sugerida and saida.categoria_sugerida != (chamado.get("categoria") or ""):
        sugestoes.append(
            f"Categoria sugerida: {saida.categoria_sugerida}"
            f" (escolhida: {chamado.get('categoria') or '—'})"
        )
    if saida.prioridade_sugerida and saida.prioridade_sugerida != (
        chamado.get("prioridade") or ""
    ):
        sugestoes.append(
            f"Prioridade sugerida: {saida.prioridade_sugerida}"
            f" (declarada: {chamado.get('prioridade') or '—'})"
        )
    if sugestoes:
        partes += ["", *sugestoes]

    if semelhantes:
        partes += ["", "Casos semelhantes já resolvidos:"]
        for s in semelhantes:
            linha = f"- {s.get('codigo') or '?'} — {s.get('titulo') or ''}".rstrip(" —")
            resolucao = _resumir_resolucao(s.get("resolucao"))
            if resolucao:
                linha += f" · resolução registrada: {resolucao}"
            partes.append(linha)

    if not saida.informacoes_suficientes and saida.perguntas:
        partes += ["", "Informações faltantes — perguntas que a IA faria ao autor:"]
        partes += [f"{i}. {p}" for i, p in enumerate(saida.perguntas, start=1)]

    partes += ["", f"Confiança: {saida.confianca} · Gerado por IA — valide antes de agir."]
    return "\n".join(partes)


def montar_pergunta_publica(saida: SaidaTriagem, chamado: dict[str, Any]) -> str:
    """Mensagem pública ao autor com as perguntas da triagem (função pura)."""
    codigo = chamado.get("codigo") or ""
    partes = [
        "Olá! Sou o assistente virtual do suporte"
        + (f" (chamado {codigo})" if codigo else "")
        + ". Para agilizar seu atendimento, poderia responder:",
        "",
    ]
    partes += [f"{i}. {p}" for i, p in enumerate(saida.perguntas, start=1)]
    partes += [
        "",
        "Basta responder aqui no chamado ou a este e-mail. "
        "Um atendente dará sequência assim que possível.",
    ]
    return "\n".join(partes)


async def _salvar_nota_interna(conn: Any, chamado_id: str, remetente_id: str, conteudo: str) -> None:
    """Persiste a pré-análise SEMPRE como nota interna.

    Invariante da Seção 8.2: ``is_interna`` NÃO é parâmetro — é ``true`` fixado
    no SQL. Esta função é o único destino da nota; o canal público é
    :func:`_enviar_pergunta_publica`, usado exclusivamente na ação PERGUNTAS.
    """
    await conn.execute(
        "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos) "
        "VALUES ($1::uuid, $2::uuid, $3, true, '[]'::jsonb)",
        chamado_id,
        remetente_id,
        conteudo,
    )


async def _enviar_pergunta_publica(
    conn: Any, chamado_id: str, remetente_id: str, conteudo: str
) -> None:
    """Mensagem pública ao autor (ação PERGUNTAS, F2). ``is_interna=false``
    fixado — este é o único caminho do motor que escreve no canal público."""
    await conn.execute(
        "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos) "
        "VALUES ($1::uuid, $2::uuid, $3, false, '[]'::jsonb)",
        chamado_id,
        remetente_id,
        conteudo,
    )


async def _obter_perfil_ia_id(conn: Any) -> str | None:
    global _perfil_ia_id
    if _perfil_ia_id is None:
        _perfil_ia_id = await conn.fetchval(
            "SELECT id::text FROM perfis WHERE nome = $1 LIMIT 1", PERFIL_IA_NOME
        )
    return _perfil_ia_id


def _soma_tokens(acumulado: int | None, novo: int | None) -> int | None:
    if novo is None:
        return acumulado
    return (acumulado or 0) + novo


async def _chamar_modelo(
    mensagens: list[dict[str, str]], settings: Settings
) -> tuple[SaidaTriagem | None, str | None, int | None, int | None]:
    """Uma chamada estruturada + 1 retry em JSON inválido (Seção 2.1).

    Devolve ``(saida, erro, tokens_entrada, tokens_saida)`` — tokens somados
    entre tentativas (custo real). Falha de provedor não tem retry: vira erro
    silencioso direto (a triagem nunca segura a abertura)."""
    tokens_in: int | None = None
    tokens_out: int | None = None
    erro: str | None = None
    for _tentativa in (1, 2):
        try:
            resposta = await cliente.completar_chat(
                mensagens=mensagens,
                model=settings.ia_triagem_model,
                api_key=settings.ia_triagem_api_key,
                base_url=settings.ia_triagem_base_url,
                timeout_s=settings.ia_triagem_timeout_s,
                max_tokens=_MAX_TOKENS_SAIDA,
                json_mode=True,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return None, f"provedor: {exc}", tokens_in, tokens_out
        tokens_in = _soma_tokens(tokens_in, resposta.tokens_entrada)
        tokens_out = _soma_tokens(tokens_out, resposta.tokens_saida)
        try:
            return SaidaTriagem.model_validate_json(resposta.conteudo), None, tokens_in, tokens_out
        except ValidationError as exc:
            erro = f"json_invalido: {exc.error_count()} erro(s) de schema"
            continue
    return None, erro, tokens_in, tokens_out


# Referências fortes às tasks disparadas (asyncio só guarda referência fraca:
# sem isso, o GC pode matar a triagem no meio — recomendação da própria doc).
_tasks_ativas: set[asyncio.Task] = set()


def agendar_triagem(chamado_id: str) -> None:
    """Dispara a triagem AGORA, em paralelo à resposta (``asyncio.create_task``).

    Por que não ``BackgroundTasks`` (otimização de latência, 2026-07-23): o
    Starlette só roda a task depois que a resposta atravessa TODA a cadeia de
    middlewares e é consumida — qualquer coisa presa nesse caminho (middleware,
    proxy, cliente lento) segura a triagem junto. Em produção as 2 primeiras
    triagens da sombra saíram 8 e 28 min após a abertura com o modelo levando
    ~4 s (meta p95 < 2 min, Seção 9). Com ``create_task`` a triagem começa no
    instante do agendamento, independente do ciclo da resposta; o motor
    revalida tudo no banco, então rodar "cedo demais" é seguro por construção.
    """
    task = asyncio.create_task(executar_triagem(chamado_id, agendada_em=time.time()))
    _tasks_ativas.add(task)
    task.add_done_callback(_tasks_ativas.discard)


async def executar_triagem(chamado_id: str, agendada_em: float | None = None) -> None:
    """Entrada da background task — tolerante a falha ponta a ponta (Regra #5).

    Chamada tanto na abertura (rodada 1) quanto na resposta do autor
    (re-triagem, F2): a rodada certa é derivada do banco.

    ``agendada_em`` (``time.time()`` de quem agendou): instrumentação do gap
    agendamento→execução — em produção (2026-07-23) as duas primeiras triagens
    reais saíram 8 e 28 min após a abertura com `duracao_ms` de ~4 s, ou seja,
    o tempo se perde ANTES da task rodar (meta p95 < 2 min, Seção 9). O log
    permite atribuir o atraso à infra (deploy/loop de eventos) com dado real."""
    if agendada_em is not None:
        espera_s = time.time() - agendada_em
        nivel = log.warning if espera_s > 60 else log.info
        nivel(
            "[IA TRIAGEM] Task do chamado %s iniciou %.1fs após o agendamento.",
            chamado_id,
            espera_s,
        )
    try:
        await _executar(chamado_id)
    except Exception as exc:  # noqa: BLE001 — task de fundo nunca derruba nada
        log.warning("[IA TRIAGEM] Erro inesperado no chamado %s: %s", chamado_id, exc)


async def _executar(chamado_id: str) -> None:
    settings = get_settings()
    if not settings.ia_triagem_ativa or not settings.ia_triagem_api_key:
        return  # kill switch: nenhum efeito colateral (Seção 8.2, teste 5)

    # 1) Contexto + máquina de rodadas — conexão curta; a chamada HTTP fica FORA.
    t_inicio_db = time.monotonic()
    async with admin_connection() as conn:
        espera_pool_s = time.monotonic() - t_inicio_db
        if espera_pool_s > 5:
            # Pool saturado/Supavisor lento é candidato a explicar gaps grandes.
            log.warning(
                "[IA TRIAGEM] Chamado %s esperou %.1fs por conexão do pool.",
                chamado_id,
                espera_pool_s,
            )
        chamado = await conn.fetchrow(
            """
            SELECT c.id::text AS id, c.codigo, c.titulo, c.descricao,
                   c.status::text AS status, c.cliente_id, c.operador_id,
                   c.prioridade::text AS prioridade, c.dados_formulario,
                   c.departamento_id, cat.nome AS categoria, d.nome AS departamento
            FROM chamados c
            JOIN departamentos d ON d.id = c.departamento_id
            LEFT JOIN categorias cat ON cat.id = c.categoria_id
            WHERE c.id = $1::uuid
            """,
            chamado_id,
        )
        if chamado is None:
            return
        chamado = dict(chamado)
        if chamado["departamento"] not in settings.ia_triagem_departamentos_lista:
            return
        # Guarda de atendimento (Seção 2.3, ajuste 2026-07-23): atendente atuando
        # NÃO suprime a pré-análise da rodada 1 — a nota interna é para quem
        # atende. O que fica vetado é interpelar o autor (PERGUNTAS, decidido em
        # `decidir_acao`) e re-triagem. Chamado encerrado não é triado.
        if chamado["status"] == "RESOLVIDO":
            return
        atendimento_iniciado = (
            chamado["status"] != "NOVO" or chamado["operador_id"] is not None
        )

        # Rodada derivada do banco (idempotência por construção).
        info = await conn.fetchrow(
            """
            SELECT COALESCE(MAX(t.rodada), 0) AS ultima_rodada,
                   (SELECT t2.acao FROM ia_triagens t2 WHERE t2.chamado_id = $1::uuid
                     ORDER BY t2.rodada DESC, t2.id DESC LIMIT 1) AS ultima_acao,
                   (SELECT t2.created_at FROM ia_triagens t2 WHERE t2.chamado_id = $1::uuid
                     ORDER BY t2.rodada DESC, t2.id DESC LIMIT 1) AS ultima_em
              FROM ia_triagens t WHERE t.chamado_id = $1::uuid
            """,
            chamado_id,
        )
        rodada = int(info["ultima_rodada"]) + 1
        if rodada > settings.ia_triagem_max_rodadas:
            return  # teto: a rodada N+1 além do máximo é impossível (Regra #3)
        conversa: list[dict[str, str]] = []
        if rodada > 1:
            # Re-triagem só enquanto nenhum atendente atua (Seção 2.3).
            if atendimento_iniciado:
                return
            # Re-triagem só faz sentido se a IA perguntou E o autor respondeu
            # DEPOIS da pergunta (task duplicada/replay não re-tria).
            if info["ultima_acao"] != "PERGUNTAS":
                return
            respondeu = await conn.fetchval(
                """
                SELECT 1 FROM mensagens
                 WHERE chamado_id = $1::uuid AND remetente_id = $2
                   AND is_interna = false AND created_at > $3
                 LIMIT 1
                """,
                chamado_id,
                chamado["cliente_id"],
                info["ultima_em"],
            )
            if not respondeu:
                return
            conversa = [
                {
                    "papel": "Autor" if r["remetente_id"] == chamado["cliente_id"] else "Equipe",
                    "conteudo": r["conteudo"],
                }
                for r in await conn.fetch(
                    """
                    SELECT m.conteudo, m.remetente_id FROM mensagens m
                     WHERE m.chamado_id = $1::uuid AND m.is_interna = false
                       AND m.conteudo <> ''
                     ORDER BY m.created_at
                    """,
                    chamado_id,
                )
            ]
        categorias = [
            r["nome"]
            for r in await conn.fetch(
                "SELECT nome FROM categorias WHERE departamento_id = $1 AND ativo ORDER BY nome",
                chamado["departamento_id"],
            )
        ]

    if isinstance(chamado.get("dados_formulario"), str):  # jsonb chega como str no asyncpg
        chamado["dados_formulario"] = json.loads(chamado["dados_formulario"])

    # 2) Uma chamada estruturada (+1 retry de JSON) — custo previsível (Regra #9).
    inicio = time.monotonic()
    saida, erro, tokens_in, tokens_out = await _chamar_modelo(
        montar_mensagens(chamado, categorias, conversa or None), settings
    )
    duracao_ms = int((time.monotonic() - inicio) * 1000)

    # 3) Persistência atômica: auditoria + mensagem (nota OU pergunta) + histórico.
    email_pergunta: str | None = None
    async with admin_connection() as conn:
        perfil_id = await _obter_perfil_ia_id(conn)
        if saida is not None and perfil_id is None:
            saida, erro = None, "perfil_assistente_ia_ausente"
        # Reavalia a guarda com estado FRESCO (Seção 2.3): se o atendente assumiu
        # durante a chamada de modelo, a pergunta pública vira nota interna.
        estado = await conn.fetchrow(
            "SELECT status::text AS status, operador_id FROM chamados WHERE id = $1::uuid",
            chamado_id,
        )
        if estado is not None:
            atendimento_iniciado = (
                estado["status"] != "NOVO" or estado["operador_id"] is not None
            )
        acao = (
            decidir_acao(saida, rodada, settings, atendimento_iniciado)
            if saida is not None
            else "ERRO"
        )
        # Busca de semelhantes (F3): só quando a ação é nota interna (a pergunta
        # pública não cita casos) e o modelo devolveu termos. Falha da busca é
        # silenciosa — a nota sai sem a seção, nunca deixa de sair (Regra #5).
        semelhantes: list[dict[str, Any]] = []
        if saida is not None and acao == "NOTA_INTERNA" and saida.termos_busca:
            try:
                semelhantes = await ia_busca.buscar_semelhantes(
                    conn,
                    departamento_id=chamado["departamento_id"],
                    chamado_id=chamado_id,
                    termos=saida.termos_busca,
                )
            except Exception as exc:  # noqa: BLE001 — busca nunca derruba a triagem
                log.warning(
                    "[IA TRIAGEM] Busca de semelhantes falhou no chamado %s: %s",
                    chamado_id,
                    exc,
                )
        resultado = saida.model_dump() if saida is not None else {"erro": erro or "desconhecido"}
        if semelhantes:
            # Auditável em `ia_triagens.resultado`: QUAIS casos foram citados.
            resultado["semelhantes_codigos"] = [s.get("codigo") for s in semelhantes]
        triagem_id = await conn.fetchval(
            """
            INSERT INTO ia_triagens
              (chamado_id, rodada, passe, acao, resultado, modelo,
               tokens_entrada, tokens_saida, custo_usd, duracao_ms)
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
            ON CONFLICT (chamado_id, rodada, passe) DO NOTHING
            RETURNING id
            """,
            chamado_id,
            rodada,
            _PASSE_UNICO,
            acao,
            json.dumps(resultado, ensure_ascii=False),
            settings.ia_triagem_model,
            tokens_in,
            tokens_out,
            cliente.custo_usd(settings.ia_triagem_model, tokens_in, tokens_out),
            duracao_ms,
        )
        if triagem_id is None:
            # Corrida com outra execução (retry duplicado): quem chegou antes já
            # gravou a mensagem — não duplica (UNIQUE + verificação de rodada).
            return
        if saida is None:
            log.warning("[IA TRIAGEM] Chamado %s sem triagem útil: %s", chamado_id, erro)
            return
        if acao == "PERGUNTAS":
            # Ciclo de perguntas primeiro (decisão do usuário 2026-07-23): a
            # nota interna fica para o fim do ciclo — rodada N+1 (resposta do
            # autor) ou teto de rodadas, com as lacunas sinalizadas.
            email_pergunta = montar_pergunta_publica(saida, chamado)
            await _enviar_pergunta_publica(conn, chamado_id, perfil_id, email_pergunta)
        else:
            await _salvar_nota_interna(
                conn, chamado_id, perfil_id, montar_nota(saida, chamado, semelhantes)
            )
        await conn.execute(
            """
            INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
            VALUES ($1::uuid, $2::uuid, 'IA_TRIAGEM', $3::jsonb)
            """,
            chamado_id,
            perfil_id,
            json.dumps(
                {
                    "rodada": rodada,
                    "passe": _PASSE_UNICO,
                    "acao": acao,
                    "modelo": settings.ia_triagem_model,
                    "modo_sombra": settings.ia_triagem_modo_sombra,
                }
            ),
        )

    # 4) E-mail da pergunta (fora da transação — a mensagem já está visível).
    #    Remetente = perfil IA (≠ autor) ⇒ o destinatário resolvido é o AUTOR,
    #    com Reply-To inbound (a resposta por e-mail reagenda a triagem).
    if email_pergunta is not None:
        try:
            await notificar_nova_mensagem_email(chamado, str(perfil_id), email_pergunta)
        except Exception as exc:  # noqa: BLE001 — e-mail nunca derruba a triagem
            log.warning("[IA TRIAGEM] Falha ao notificar pergunta por e-mail: %s", exc)

    log.info("[IA TRIAGEM] Chamado %s triado (rodada %s, %s ms).", chamado_id, rodada, duracao_ms)

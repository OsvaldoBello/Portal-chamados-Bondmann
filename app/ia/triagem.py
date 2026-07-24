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
from app.ia import cliente, contexto_quimico
from app.ia.schemas import SaidaPasseB, SaidaTriagem
from app.notification import notificar_nova_mensagem_email
from app.repositories import ia_busca

log = logging.getLogger("app.ia_triagem")

PERFIL_IA_NOME = "Assistente IA"
_PASSE_UNICO = "UNICO"
_MAX_TOKENS_SAIDA = 900
# Passe B (F4): pré-análise 6M é mais longa que o JSON de triagem.
_MAX_TOKENS_SAIDA_B = 1400
# Departamento com agente de DOIS PASSES (Seção 3 do plano IA). Os demais usam
# o passe único. Nome canônico do banco (migrations 0027/0049).
_DEPTO_QUIMICO = "Dpto Químico"

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
    *,
    prompt_nome: str = "ti",
    playbook: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Mensagens system+user do passe único / Passe A (função pura, testável).

    ``conversa`` (rodadas > 1): lista de ``{"papel": ..., "conteudo": ...}`` com
    a troca pública até aqui — perguntas da IA e respostas do autor.

    ``playbook`` (Químico, F4): roteiro de perguntas de investigação por
    cenário — SEM qualquer dado de produto/ficha (invariante do Passe A,
    Seção 3.1). É a única informação da base que este payload pode conter."""
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
    if playbook:
        linhas += ["", "## Roteiro de investigação (perguntas por cenário)"]
        for item in playbook:
            detalhes = [str(item.get("cenario") or "")]
            pergunta = item.get("Pergunta obrigatória da IA")
            if pergunta:
                detalhes.append(f"pergunta: {pergunta}")
            alerta = item.get("Resposta crítica / alerta")
            if alerta:
                detalhes.append(f"alerta: {alerta}")
            linhas.append("- " + " · ".join(d for d in detalhes if d))
    if categorias:
        linhas += ["", "## Catálogo de categorias do departamento"]
        linhas += [f"- {nome}" for nome in categorias]
    return [
        {"role": "system", "content": _prompt(prompt_nome)},
        {"role": "user", "content": "\n".join(linhas)},
    ]


def montar_mensagens_passe_b(
    chamado: dict[str, Any],
    conversa: list[dict[str, str]] | None,
    contexto_texto: str,
) -> list[dict[str, str]]:
    """Mensagens system+user do Passe B do Químico (função pura, testável).

    ``contexto_texto`` é a recuperação seletiva formatada por
    :func:`app.ia.contexto_quimico.formatar_contexto` — produto(s) citado(s)
    sem proporções, ficha técnica, diagnósticos e regras de sigilo. Vazio =
    base indisponível/produto não identificado (o passe analisa só o chamado).
    """
    linhas = [
        "## Chamado",
        f"Categoria: {chamado.get('categoria') or '—'}",
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
        linhas += ["", "## Conversa pública até agora"]
        linhas += [f"[{m['papel']}] {m['conteudo']}" for m in conversa]
    if contexto_texto:
        linhas += ["", contexto_texto]
    else:
        linhas += [
            "",
            "## Base interna",
            "Indisponível nesta execução — analise apenas com os dados do chamado "
            "e sinalize explicitamente essa limitação.",
        ]
    return [
        {"role": "system", "content": _prompt("quimico_passe_b")},
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

    sugestoes = _secao_sugestoes(saida, chamado)
    if sugestoes:
        partes += ["", *sugestoes]

    partes += _secao_semelhantes(semelhantes)

    if not saida.informacoes_suficientes and saida.perguntas:
        partes += ["", "Informações faltantes — perguntas que a IA faria ao autor:"]
        partes += [f"{i}. {p}" for i, p in enumerate(saida.perguntas, start=1)]

    partes += ["", f"Confiança: {saida.confianca} · Gerado por IA — valide antes de agir."]
    return "\n".join(partes)


def _secao_sugestoes(saida: SaidaTriagem, chamado: dict[str, Any]) -> list[str]:
    """Sugestões de categoria/prioridade APENAS quando divergem (Regra #3)."""
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
    return sugestoes


def _secao_semelhantes(semelhantes: list[dict[str, Any]] | None) -> list[str]:
    """Seção "Casos semelhantes" (F3) — vazia quando não há (nunca inventada)."""
    if not semelhantes:
        return []
    partes = ["", "Casos semelhantes já resolvidos:"]
    for s in semelhantes:
        linha = f"- {s.get('codigo') or '?'} — {s.get('titulo') or ''}".rstrip(" —")
        resolucao = _resumir_resolucao(s.get("resolucao"))
        if resolucao:
            linha += f" · resolução registrada: {resolucao}"
        partes.append(linha)
    return partes


def montar_nota_quimico(
    saida_a: SaidaTriagem,
    saida_b: SaidaPasseB,
    chamado: dict[str, Any],
    semelhantes: list[dict[str, Any]] | None = None,
) -> str:
    """Nota interna do Químico (F4): pré-análise técnica do Passe B + triagem
    do Passe A (função pura).

    A pré-análise 6M vem do Passe B (que leu a base sigilosa); as sugestões de
    categoria/prioridade vêm do Passe A (que fez a triagem). Este texto é
    persistido EXCLUSIVAMENTE via :func:`_salvar_nota_interna` (invariante 8.2).
    """
    partes = [
        "Triagem automática — pré-análise do Assistente Químico (IA)",
        "",
        saida_b.pre_analise.strip(),
    ]
    sugestoes = _secao_sugestoes(saida_a, chamado)
    if sugestoes:
        partes += ["", *sugestoes]
    if saida_b.escalar_para_quimico:
        partes += ["", "⚠️ Playbook indica ESCALAR este caso ao químico responsável."]
    if saida_b.dados_faltantes:
        partes += ["", "Dados a confirmar antes de concluir:"]
        partes += [f"- {d}" for d in saida_b.dados_faltantes]
    partes += _secao_semelhantes(semelhantes)
    partes += ["", f"Confiança: {saida_b.confianca} · Gerado por IA — valide antes de agir."]
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
    mensagens: list[dict[str, str]],
    settings: Settings,
    *,
    model: str | None = None,
    schema: type = SaidaTriagem,
    max_tokens: int = _MAX_TOKENS_SAIDA,
) -> tuple[Any | None, str | None, int | None, int | None]:
    """Uma chamada estruturada + 1 retry em JSON inválido (Seção 2.1).

    Devolve ``(saida, erro, tokens_entrada, tokens_saida)`` — tokens somados
    entre tentativas (custo real). Falha de provedor não tem retry: vira erro
    silencioso direto (a triagem nunca segura a abertura).

    ``model``/``schema``/``max_tokens`` parametrizam o passe (F4): o Passe B do
    Químico valida :class:`SaidaPasseB` e pode usar ``IA_TRIAGEM_MODEL_PASSE_B``.
    """
    tokens_in: int | None = None
    tokens_out: int | None = None
    erro: str | None = None
    for _tentativa in (1, 2):
        try:
            resposta = await cliente.completar_chat(
                mensagens=mensagens,
                model=model or settings.ia_triagem_model,
                api_key=settings.ia_triagem_api_key,
                base_url=settings.ia_triagem_base_url,
                timeout_s=settings.ia_triagem_timeout_s,
                max_tokens=max_tokens,
                json_mode=True,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return None, f"provedor: {exc}", tokens_in, tokens_out
        tokens_in = _soma_tokens(tokens_in, resposta.tokens_entrada)
        tokens_out = _soma_tokens(tokens_out, resposta.tokens_saida)
        try:
            return schema.model_validate_json(resposta.conteudo), None, tokens_in, tokens_out
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

    # 2) Passe único (TI) ou Passe A (Químico, F4) — uma chamada estruturada
    #    (+1 retry de JSON), custo previsível (Regra #9). O Passe A do Químico
    #    recebe SÓ o chamado + o playbook de perguntas — nunca a base sigilosa
    #    (invariante 8.2; o playbook não contém dado de produto).
    eh_quimico = chamado["departamento"] == _DEPTO_QUIMICO
    playbook = await contexto_quimico.playbook_perguntas(settings) if eh_quimico else []
    inicio = time.monotonic()
    saida, erro, tokens_in, tokens_out = await _chamar_modelo(
        montar_mensagens(
            chamado,
            categorias,
            conversa or None,
            prompt_nome="quimico_passe_a" if eh_quimico else "ti",
            playbook=playbook or None,
        ),
        settings,
    )
    duracao_ms = int((time.monotonic() - inicio) * 1000)

    # 2b) Passe B do Químico (F4): só quando a rodada vai fechar em NOTA
    #     INTERNA (a pergunta pública nunca passa pelo canal com base sigilosa).
    #     A decisão aqui usa o estado carregado no passo 1; a guarda definitiva
    #     é reavaliada na persistência — ela só muda no sentido PERGUNTAS →
    #     NOTA_INTERNA, caso em que a nota degrada para o fallback do Passe A.
    saida_b: SaidaPasseB | None = None
    erro_b: str | None = None
    tokens_b_in: int | None = None
    tokens_b_out: int | None = None
    duracao_b_ms: int | None = None
    passe_b_tentado = False
    modelo_b = settings.ia_triagem_model_passe_b or settings.ia_triagem_model
    if (
        eh_quimico
        and saida is not None
        and decidir_acao(saida, rodada, settings, atendimento_iniciado) == "NOTA_INTERNA"
    ):
        passe_b_tentado = True
        ctx = await contexto_quimico.montar_contexto_passe_b(
            settings,
            (chamado.get("dados_formulario") or {}).get("produto"),
            f"{chamado.get('titulo') or ''}\n{chamado.get('descricao') or ''}",
        )
        contexto_texto = contexto_quimico.formatar_contexto(ctx) if ctx is not None else ""
        inicio_b = time.monotonic()
        saida_b, erro_b, tokens_b_in, tokens_b_out = await _chamar_modelo(
            montar_mensagens_passe_b(chamado, conversa or None, contexto_texto),
            settings,
            model=modelo_b,
            schema=SaidaPasseB,
            max_tokens=_MAX_TOKENS_SAIDA_B,
        )
        duracao_b_ms = int((time.monotonic() - inicio_b) * 1000)

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
        passe_principal = "A" if eh_quimico else _PASSE_UNICO
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
            passe_principal,
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
        if passe_b_tentado:
            # Auditoria do Passe B (Seção 4.1): METADADOS apenas — o texto da
            # pré-análise vive só na `mensagens` interna (RLS validada), nunca
            # em `resultado` (evita um segundo lugar sensível a proteger).
            resultado_b = (
                {
                    "confianca": saida_b.confianca,
                    "escalar_para_quimico": saida_b.escalar_para_quimico,
                    "produto_reconhecido": saida_b.produto_reconhecido,
                    "dados_faltantes": saida_b.dados_faltantes,
                }
                if saida_b is not None
                else {"erro": erro_b or "desconhecido"}
            )
            await conn.fetchval(
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
                "B",
                "NOTA_INTERNA" if saida_b is not None else "ERRO",
                json.dumps(resultado_b, ensure_ascii=False),
                modelo_b,
                tokens_b_in,
                tokens_b_out,
                cliente.custo_usd(modelo_b, tokens_b_in, tokens_b_out),
                duracao_b_ms,
            )
        if saida is None:
            log.warning("[IA TRIAGEM] Chamado %s sem triagem útil: %s", chamado_id, erro)
            return
        if acao == "PERGUNTAS":
            # Ciclo de perguntas primeiro (decisão do usuário 2026-07-23): a
            # nota interna fica para o fim do ciclo — rodada N+1 (resposta do
            # autor) ou teto de rodadas, com as lacunas sinalizadas.
            email_pergunta = montar_pergunta_publica(saida, chamado)
            await _enviar_pergunta_publica(conn, chamado_id, perfil_id, email_pergunta)
        elif eh_quimico and saida_b is not None:
            # Nota do Químico: pré-análise do Passe B (com base sigilosa) +
            # triagem do Passe A. Persistência SEMPRE via _salvar_nota_interna
            # (is_interna=true fixado — invariante 8.2).
            await _salvar_nota_interna(
                conn, chamado_id, perfil_id, montar_nota_quimico(saida, saida_b, chamado, semelhantes)
            )
        else:
            # TI, ou fallback do Químico com Passe B indisponível (degradação
            # graciosa — a nota do Passe A sai sem a base, nunca deixa de sair).
            if passe_b_tentado and saida_b is None:
                log.warning(
                    "[IA TRIAGEM] Passe B falhou no chamado %s (%s) — nota via Passe A.",
                    chamado_id,
                    erro_b,
                )
            await _salvar_nota_interna(
                conn, chamado_id, perfil_id, montar_nota(saida, chamado, semelhantes)
            )
        detalhes_historico = {
            "rodada": rodada,
            "passe": passe_principal,
            "acao": acao,
            "modelo": settings.ia_triagem_model,
            "modo_sombra": settings.ia_triagem_modo_sombra,
        }
        if passe_b_tentado:
            detalhes_historico["passe_b"] = "NOTA_INTERNA" if saida_b is not None else "ERRO"
        await conn.execute(
            """
            INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
            VALUES ($1::uuid, $2::uuid, 'IA_TRIAGEM', $3::jsonb)
            """,
            chamado_id,
            perfil_id,
            json.dumps(detalhes_historico),
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


# ------------------------------------------------------------------
# Reconciliação (rede de segurança do agendamento em memória)
# ------------------------------------------------------------------


async def _chamados_orfaos(conn: Any, settings: Settings) -> list[str]:
    """IDs de chamados com uma rodada de triagem "presa" (nunca rodou).

    ``agendar_triagem`` dispara via ``asyncio.create_task`` — rápido (latência
    p95 < 2 min), mas NÃO durável: um restart/redeploy do processo entre o
    agendamento e o primeiro INSERT em ``ia_triagens`` apaga a tarefa sem
    deixar rastro algum (caso real: BOND-2026-00593, 2026-07-23 — chamado de
    TI elegível com ZERO linhas em ``ia_triagens``/``historico_chamados``,
    enquanto o chamado imediatamente anterior e o seguinte foram triados
    normalmente em poucos segundos). Duas formas de "órfão", espelhando as
    mesmas guardas de :func:`_executar`:

    1. rodada 1 nunca rodou (chamado criado há mais de 3 min, nenhuma linha
       em ``ia_triagens``);
    2. a IA perguntou (rodada N), o autor respondeu há mais de 3 min, e a
       rodada N+1 nunca rodou (mesma guarda de re-triagem: ``NOVO`` + sem
       operador + dentro do teto de rodadas).

    A margem de 3 min evita corrida com o ``create_task`` recém-agendado de
    um chamado que acabou de abrir ou responder.
    """
    departamentos = settings.ia_triagem_departamentos_lista
    if not departamentos:
        return []
    linhas = await conn.fetch(
        """
        SELECT c.id::text AS id
          FROM chamados c
          JOIN departamentos d ON d.id = c.departamento_id
         WHERE d.nome = ANY($1::text[])
           AND c.status <> 'RESOLVIDO'
           AND c.created_at < now() - interval '3 minutes'
           AND NOT EXISTS (SELECT 1 FROM ia_triagens t WHERE t.chamado_id = c.id)
        UNION
        SELECT c.id::text AS id
          FROM chamados c
          JOIN departamentos d ON d.id = c.departamento_id
          JOIN LATERAL (
                 SELECT t.rodada, t.acao, t.created_at
                   FROM ia_triagens t
                  WHERE t.chamado_id = c.id
                  ORDER BY t.rodada DESC, t.id DESC
                  LIMIT 1
               ) ultima ON true
         WHERE d.nome = ANY($1::text[])
           AND c.status = 'NOVO' AND c.operador_id IS NULL
           AND ultima.acao = 'PERGUNTAS'
           AND ultima.rodada < $2
           AND EXISTS (
                 SELECT 1 FROM mensagens m
                  WHERE m.chamado_id = c.id AND m.remetente_id = c.cliente_id
                    AND m.is_interna = false AND m.created_at > ultima.created_at
                    AND m.created_at < now() - interval '3 minutes'
               )
        """,
        departamentos,
        settings.ia_triagem_max_rodadas,
    )
    return [r["id"] for r in linhas]


async def reconciliar_triagens_perdidas() -> int:
    """Varredura periódica: reexecuta a triagem dos chamados órfãos.

    Segura por construção — ``executar_triagem`` reavalida tudo no banco e é
    idempotente (Seção 2.3); processar de novo um chamado que já foi
    resolvido por outra via (corrida com o agendamento original) é um no-op
    barato (uma consulta, sem chamar o modelo). Nunca lança — mesma tolerância
    a falha do motor (Regra #5). Devolve quantos chamados foram reprocessados
    (só para log/observação)."""
    settings = get_settings()
    if not settings.ia_triagem_ativa or not settings.ia_triagem_api_key:
        return 0
    async with admin_connection() as conn:
        ids = await _chamados_orfaos(conn, settings)
    for chamado_id in ids:
        await executar_triagem(chamado_id)
    if ids:
        log.warning(
            "[IA TRIAGEM] Reconciliação reexecutou %d chamado(s) órfão(s): %s",
            len(ids),
            ", ".join(ids),
        )
    return len(ids)


async def _loop_reconciliacao(intervalo_s: float) -> None:
    """Roda :func:`reconciliar_triagens_perdidas` a cada ``intervalo_s``, até
    a task ser cancelada no shutdown do app (Regra #5: nunca derruba nada)."""
    while True:
        await asyncio.sleep(intervalo_s)
        try:
            await reconciliar_triagens_perdidas()
        except Exception as exc:  # noqa: BLE001 — loop de fundo nunca morre por erro de uma volta
            log.warning("[IA TRIAGEM] Ciclo de reconciliação falhou: %s", exc)


def iniciar_reconciliacao(settings: Settings) -> asyncio.Task | None:
    """Inicia o loop de reconciliação (chamado pelo lifespan do app).

    ``None`` se a triagem estiver desligada ou o intervalo for ``<= 0`` — a
    rede de segurança segue o mesmo kill switch geral (Regra de Ouro #5): sem
    ``IA_TRIAGEM_ATIVA``, nem a varredura roda."""
    if not settings.ia_triagem_ativa or settings.ia_triagem_reconciliacao_intervalo_s <= 0:
        return None
    return asyncio.create_task(_loop_reconciliacao(settings.ia_triagem_reconciliacao_intervalo_s))

"""Intake de chamados via WhatsApp com IA como intérprete.

Fluxo: mensagem chega no webhook (``app/routes/whatsapp.py``) → o telefone é
resolvido contra ``perfis.telefone_normalizado`` (migration 0085) → a conversa
acumula no banco → uma chamada com saída estruturada
(:class:`app.ia.schemas.SaidaWhatsAppIntake`) decide se já dá para abrir o
chamado ou se falta perguntar algo → o chamado é criado em nome do PRÓPRIO
usuário identificado e ele recebe a confirmação com o código por WhatsApp.

Garantias estruturais (mesmas Regras de Ouro do motor de triagem):

- **Kill switch sem efeito colateral**: ``WHATSAPP_INTAKE_ATIVO=false`` (default)
  ⇒ o webhook segue só logando, nada é gravado nem enviado.
- **Só número cadastrado abre chamado**: telefone que não casa com exatamente
  UM perfil ativo recebe orientação de cadastro e nada mais acontece (decisão
  de produto — não existe abertura anônima).
- **RLS continua sendo a barreira real**: o chamado é criado por
  ``AtendimentoRepo.criar`` sob ``rls_connection`` com claims sintéticas do
  perfil resolvido (:func:`app.services.whatsapp_intake_claims.claims_do_perfil`),
  não por ``admin_connection`` — bug de cálculo de ``cliente_id`` é rejeitado
  pelo Postgres. ``admin_connection`` fica só para as tabelas de estado e
  auditoria próprias, e para a resolução telefone→perfil (pré-autenticação).
- **Nunca inventa destino**: departamento/categoria/subcategoria vindos do
  modelo têm que casar com o catálogo real do banco; qualquer nome fora dele
  encerra a conversa sem chamado, nunca vira INSERT com FK alucinada.
- **Falha silenciosa ponta a ponta**: nada aqui derruba o webhook (que precisa
  responder 200 rápido para a Meta não reentregar).
- **Idempotência**: ``wamid`` único em ``whatsapp_mensagens_recebidas`` absorve
  a reentrega de webhook da Meta; o lock otimista em ``whatsapp_conversas``
  absorve tasks concorrentes; ``UNIQUE(conversa_id, rodada)`` em
  ``ia_whatsapp_intake`` absorve reprocessamento da reconciliação.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.db import admin_connection
from app.ia import anexos_contexto, cliente
from app.ia.catalogo_prompt import linha_catalogo
from app.ia.chamada_estruturada import chamar_modelo_estruturado
from app.ia.schemas import SaidaWhatsAppIntake
from app.services.whatsapp_intake_claims import claims_do_perfil

log = logging.getLogger("app.ia_whatsapp_intake")

_MAX_TOKENS_SAIDA = 900
_ORIGEM_DEMANDA = "WhatsApp"
# Margem antes de considerar uma conversa "travada" na reconciliação — evita
# corrida com a task recém-disparada (mesmo papel de `_MARGEM_ORFAO` na
# triagem, onde 1 min já se mostrou suficiente em produção).
_MARGEM_TRAVADA = "2 minutes"

TEXTO_SEM_CADASTRO = (
    "Olá! Não encontrei este número no cadastro do Portal de Chamados da "
    "Bondmann. Para abrir chamados por aqui, peça ao TI para cadastrar o seu "
    "número no seu perfil do Portal."
)
TEXTO_ERRO_GENERICO = (
    "Não consegui registrar seu chamado agora. Tente novamente em alguns "
    "minutos ou abra pelo Portal de Chamados."
)
TEXTO_SEM_SETOR = (
    "Seu cadastro está sem setor definido, então não consigo abrir o chamado "
    "por aqui. Peça ao TI para completar seu perfil no Portal."
)


@lru_cache(maxsize=2)
def _prompt(nome: str) -> str:
    """Prompt de sistema versionado em ``app/ia/prompts/<nome>.md`` (Regra #8).

    O cabeçalho de documentação do arquivo (antes do primeiro ``---``) não é
    enviado ao modelo — mesmo formato/loader do motor de triagem."""
    texto = (Path(__file__).parent / "prompts" / f"{nome}.md").read_text(encoding="utf-8")
    return texto.split("\n---\n", 1)[1].strip()


def intake_ativo(settings: Settings) -> bool:
    """Kill switch efetivo: flag geral + chave do provedor + ao menos um
    departamento habilitado. Sem os três, nada roda (Regra de Ouro #5)."""
    return (
        settings.whatsapp_intake_ativo
        and bool(settings.ia_triagem_api_key)
        and bool(settings.whatsapp_intake_departamentos_lista)
    )


# --------------------------------------------------------------------------
# Resolução telefone → perfil
# --------------------------------------------------------------------------


async def resolver_perfil_por_telefone(telefone: str) -> dict[str, Any] | None:
    """Perfil ATIVO cujo telefone casa com o número que mandou a mensagem.

    Usa a mesma função SQL de normalização da coluna gerada (migration 0085) —
    fonte única, sem reimplementar a normalização em Python. Ela reduz o
    formato nacional do cadastro ("51994105691") e o `wa_id` canônico da Meta
    ("555194105691", com país e sem o nono dígito) à mesma forma. Devolve
    ``None`` quando não há match **ou quando há mais de um**: sem UNIQUE na
    coluna (produção tem duplicatas), assumir o primeiro abriria chamado em
    nome da pessoa errada. Roda sob ``admin_connection`` por natureza — é a
    etapa pré-autenticação que descobre QUEM é o usuário."""
    async with admin_connection() as conn:
        linhas = await conn.fetch(
            """
            SELECT p.id::text AS id,
                   p.nome,
                   p.empresa_id::text AS empresa_id,
                   p.departamento_id::text AS departamento_id,
                   d.nome AS departamento_nome
              FROM perfis p
              LEFT JOIN departamentos d ON d.id = p.departamento_id
             WHERE p.ativo = true
               AND p.telefone_normalizado = normalizar_telefone_br($1)
             LIMIT 2
            """,
            telefone,
        )
    if not linhas:
        return None
    if len(linhas) > 1:
        # Não loga o número (PII); o admin investiga pela coluna normalizada.
        log.warning(
            "[WA INTAKE] Telefone cadastrado em mais de um perfil — intake ignorado "
            "até a duplicidade ser resolvida."
        )
        return None
    return dict(linhas[0])


# --------------------------------------------------------------------------
# Recepção do webhook
# --------------------------------------------------------------------------


def extrair_mensagens(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Mensagens recebidas do payload do webhook (função pura).

    Só ``messages`` — ``statuses`` (entregue/lido) são ignorados. Tipos sem
    suporte (áudio, vídeo, documento, localização…) entram com ``corpo``
    vazio para o modelo pedir o relato por texto, em vez de sumirem em
    silêncio."""
    mensagens: list[dict[str, Any]] = []
    for entry in (payload or {}).get("entry") or []:
        for change in entry.get("changes") or []:
            valor = change.get("value") or {}
            for msg in valor.get("messages") or []:
                wamid = msg.get("id")
                de = msg.get("from")
                if not wamid or not de:
                    continue
                tipo = str(msg.get("type") or "desconhecido")
                corpo = ""
                midia_id = None
                if tipo == "text":
                    corpo = str((msg.get("text") or {}).get("body") or "")
                elif tipo == "image":
                    imagem = msg.get("image") or {}
                    midia_id = imagem.get("id")
                    corpo = str(imagem.get("caption") or "")
                mensagens.append(
                    {
                        "wamid": str(wamid),
                        "telefone": str(de),
                        "tipo": tipo,
                        "corpo": corpo,
                        "midia_id": midia_id,
                    }
                )
    return mensagens


async def processar_mensagens_whatsapp(payload: dict[str, Any] | None) -> None:
    """Persiste as mensagens recebidas e agenda o processamento.

    Chamada de dentro do handler do webhook: faz só o trabalho curto de banco
    (para a Meta receber o 200 rápido) e delega a parte cara — chamada de
    modelo, criação de chamado — para uma task. Nunca lança."""
    settings = get_settings()
    if not intake_ativo(settings):
        return
    try:
        for msg in extrair_mensagens(payload):
            await _receber_mensagem(msg)
    except Exception as exc:  # noqa: BLE001 — webhook nunca cai por causa do intake
        log.warning("[WA INTAKE] Falha ao processar mensagens do webhook: %s", exc)


async def _receber_mensagem(msg: dict[str, Any]) -> None:
    """Grava a âncora durável da mensagem e agenda a conversa (uma mensagem)."""
    telefone = msg["telefone"]
    async with admin_connection() as conn:
        recebida_id = await conn.fetchval(
            """
            INSERT INTO whatsapp_mensagens_recebidas (wamid, telefone, tipo, corpo, midia_id)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (wamid) DO NOTHING
            RETURNING id
            """,
            msg["wamid"],
            telefone,
            msg["tipo"],
            msg["corpo"],
            msg["midia_id"],
        )
        if recebida_id is None:
            # Reentrega da Meta (at-least-once): já processamos esta mensagem.
            return

        perfil = await resolver_perfil_por_telefone(telefone)
        if perfil is None:
            await conn.execute(
                "UPDATE whatsapp_mensagens_recebidas SET status='SEM_PERFIL', processado_em=now() WHERE id=$1",
                recebida_id,
            )
            _agendar(_responder(telefone, TEXTO_SEM_CADASTRO))
            return

        conversa_id = await _abrir_ou_obter_conversa(conn, perfil, telefone)
        await conn.execute(
            """
            UPDATE whatsapp_conversas
               SET mensagens_acumuladas = mensagens_acumuladas || $2::jsonb,
                   atualizada_em = now()
             WHERE id = $1::uuid
            """,
            conversa_id,
            json.dumps(
                [
                    {
                        "papel": "usuario",
                        "conteudo": msg["corpo"],
                        "wamid": msg["wamid"],
                        "midia_id": msg["midia_id"],
                    }
                ],
                ensure_ascii=False,
            ),
        )
        await conn.execute(
            "UPDATE whatsapp_mensagens_recebidas SET conversa_id=$2::uuid WHERE id=$1",
            recebida_id,
            conversa_id,
        )

    _agendar(processar_conversa(conversa_id))


async def _abrir_ou_obter_conversa(conn: Any, perfil: dict[str, Any], telefone: str) -> str:
    """Id da conversa aberta deste telefone, criando uma se não houver.

    O índice único parcial (migration 0086) garante no banco que só existe uma
    conversa em ``COLETANDO``/``PROCESSANDO`` por telefone."""
    existente = await conn.fetchval(
        """
        SELECT id::text FROM whatsapp_conversas
         WHERE telefone = $1 AND status IN ('COLETANDO', 'PROCESSANDO')
         LIMIT 1
        """,
        telefone,
    )
    if existente:
        return str(existente)
    return str(
        await conn.fetchval(
            """
            INSERT INTO whatsapp_conversas (perfil_id, telefone)
            VALUES ($1::uuid, $2)
            RETURNING id::text
            """,
            perfil["id"],
            telefone,
        )
    )


# Referências fortes às tasks disparadas (asyncio só guarda referência fraca:
# sem isso o GC pode matar o processamento no meio). Set próprio, separado do
# da triagem — módulos desacoplados.
_tasks_ativas: set[asyncio.Task] = set()


def _agendar(corotina: Any) -> None:
    task = asyncio.create_task(corotina)
    _tasks_ativas.add(task)
    task.add_done_callback(_tasks_ativas.discard)


async def _responder(telefone: str, texto: str) -> None:
    """Envia texto ao usuário — nunca lança (envio é o último elo, não pode
    derrubar o que já foi gravado)."""
    from app.whatsapp_client import enviar_mensagem_texto

    try:
        await enviar_mensagem_texto(telefone, texto)
    except Exception as exc:  # noqa: BLE001
        log.warning("[WA INTAKE] Falha ao responder no WhatsApp: %s", type(exc).__name__)


# --------------------------------------------------------------------------
# Decisão (pura) e processamento da conversa
# --------------------------------------------------------------------------


def decidir_acao_intake(
    saida: SaidaWhatsAppIntake | None, rodada: int, max_rodadas: int
) -> str:
    """``CRIAR_CHAMADO`` | ``PERGUNTA`` | ``ENCERRAR_SEM_CHAMADO`` (função pura).

    Sem saída válida do modelo ⇒ encerra (o usuário recebe orientação de usar
    o Portal). Informação suficiente ⇒ cria. Insuficiente ⇒ pergunta, desde
    que haja pergunta formulada e ainda reste rodada; no teto, encerra em vez
    de perguntar para sempre."""
    if saida is None:
        return "ENCERRAR_SEM_CHAMADO"
    if saida.informacoes_suficientes:
        return "CRIAR_CHAMADO"
    if rodada >= max_rodadas or not (saida.pergunta_esclarecimento or "").strip():
        return "ENCERRAR_SEM_CHAMADO"
    return "PERGUNTA"


def _casar(nome: str | None, itens: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Casa um nome devolvido pelo modelo com uma entrada real do catálogo
    (comparação exata, sem caixa/espaços nas pontas). ``None`` quando o nome
    não existe — é assim que alucinação de destino nunca vira FK inválida."""
    alvo = (nome or "").strip().casefold()
    if not alvo:
        return None
    return next((i for i in itens if str(i.get("nome") or "").strip().casefold() == alvo), None)


async def _montar_catalogo(claims: dict[str, str]) -> list[dict[str, Any]]:
    """Departamentos habilitados para intake, com categorias e subcategorias.

    Reaproveita ``CatalogoRepo`` (mesmas consultas/cache da abertura pelo
    Portal) sob as claims sintéticas do perfil — nenhum SQL de catálogo novo.
    ``filtrar_publico=True``: quem abre é o próprio solicitante, mesma regra
    da abertura no Portal (migration 0076)."""
    from app.repositories.catalogo import CatalogoRepo

    settings = get_settings()
    repo = CatalogoRepo()
    habilitados = settings.whatsapp_intake_departamentos_lista
    catalogo: list[dict[str, Any]] = []
    for dep in await repo.departamentos_destino_ativos(claims):
        if str(dep.get("nome") or "") not in habilitados:
            continue
        categorias = []
        for cat in await repo.categorias_ativas(
            claims, str(dep["id"]), filtrar_publico=True
        ):
            subs = await repo.subcategorias_ativas(claims, str(cat["id"]))
            categorias.append({"id": str(cat["id"]), "nome": cat["nome"], "subcategorias": subs})
        if categorias:
            catalogo.append({"id": str(dep["id"]), "nome": dep["nome"], "categorias": categorias})
    return catalogo


def montar_mensagens(
    conversa: list[dict[str, Any]],
    catalogo: list[dict[str, Any]],
    imagem_data_uri: str | None = None,
) -> list[dict[str, Any]]:
    """Mensagens system+user da extração (mesmo formato do motor de triagem).

    O catálogo entra no turno do usuário porque muda por departamento/público;
    o prompt (system) é fixo e versionado."""
    linhas = ["## Conversa no WhatsApp"]
    for item in conversa:
        papel = "usuário" if item.get("papel") == "usuario" else "assistente"
        conteudo = str(item.get("conteudo") or "").strip()
        if conteudo:
            linhas.append(f"[{papel}] {conteudo}")
        elif item.get("midia_id"):
            linhas.append(f"[{papel}] (enviou uma imagem)")
    if catalogo:
        linhas += ["", "## Catálogo disponível"]
        for dep in catalogo:
            linhas.append(f"### Departamento: {dep['nome']}")
            linhas += [linha_catalogo(cat) for cat in dep["categorias"]]

    texto = "\n".join(linhas)
    conteudo_usuario: Any = texto
    if imagem_data_uri:
        conteudo_usuario = [
            {"type": "text", "text": texto},
            {"type": "image_url", "image_url": {"url": imagem_data_uri, "detail": "low"}},
        ]
    return [
        {"role": "system", "content": _prompt("whatsapp_intake")},
        {"role": "user", "content": conteudo_usuario},
    ]


async def _imagem_da_conversa(conversa: list[dict[str, Any]], settings: Settings) -> str | None:
    """Data URI da imagem mais recente da conversa, se houver.

    Reaproveita ``anexos_contexto.redimensionar_imagem`` (função pura, mesma
    usada pela triagem) — só a origem dos bytes muda (Graph API em vez do
    Storage). Falha de download/decode devolve ``None``: a foto é contexto
    opcional, nunca motivo para travar o intake."""
    from app.whatsapp_client import baixar_midia

    midia_id = next(
        (m.get("midia_id") for m in reversed(conversa) if m.get("midia_id")), None
    )
    if not midia_id:
        return None
    baixado = await baixar_midia(str(midia_id))
    if baixado is None:
        return None
    conteudo, _mime = baixado
    return anexos_contexto.redimensionar_imagem(
        conteudo,
        max_dimensao=settings.ia_triagem_anexos_max_dimensao_px,
        qualidade=settings.ia_triagem_anexos_qualidade_jpeg,
    )


async def processar_conversa(conversa_id: str) -> None:
    """Roda uma rodada de extração e executa a ação decidida. Nunca lança."""
    try:
        await _processar_conversa(conversa_id)
    except Exception as exc:  # noqa: BLE001 — tolerância a falha ponta a ponta
        log.warning("[WA INTAKE] Conversa %s falhou: %s", conversa_id, exc)
        try:
            async with admin_connection() as conn:
                await conn.execute(
                    "UPDATE whatsapp_conversas SET status='FALHOU', atualizada_em=now() WHERE id=$1::uuid",
                    conversa_id,
                )
        except Exception:  # noqa: BLE001 — banco indisponível: a reconciliação reprocessa
            pass


async def _processar_conversa(conversa_id: str) -> None:
    settings = get_settings()
    if not intake_ativo(settings):
        return

    async with admin_connection() as conn:
        # Lock otimista: só uma task processa a conversa por vez (a outra sai
        # sem fazer nada). Complementa o `wamid` único contra reprocessamento.
        conversa = await conn.fetchrow(
            """
            UPDATE whatsapp_conversas
               SET status = 'PROCESSANDO', atualizada_em = now()
             WHERE id = $1::uuid AND status = 'COLETANDO'
         RETURNING id::text, perfil_id::text AS perfil_id, telefone, rodada,
                   mensagens_acumuladas
            """,
            conversa_id,
        )
    if conversa is None:
        return

    telefone = conversa["telefone"]
    perfil = await _perfil_por_id(conversa["perfil_id"])
    if perfil is None:  # perfil desativado no meio da conversa
        await _encerrar(conversa_id, "EXPIRADA")
        return

    mensagens_acumuladas = conversa["mensagens_acumuladas"]
    if isinstance(mensagens_acumuladas, str):
        mensagens_acumuladas = json.loads(mensagens_acumuladas)
    rodada = int(conversa["rodada"]) + 1
    claims = claims_do_perfil(perfil["id"])

    catalogo = await _montar_catalogo(claims)
    if not catalogo:
        log.warning("[WA INTAKE] Nenhum departamento habilitado tem catálogo visível.")
        await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                         TEXTO_ERRO_GENERICO, settings, {}, None, None, None)
        return

    imagem = await _imagem_da_conversa(mensagens_acumuladas, settings)
    inicio = time.monotonic()
    saida, erro, tokens_in, tokens_out = await chamar_modelo_estruturado(
        montar_mensagens(mensagens_acumuladas, catalogo, imagem),
        model=settings.whatsapp_intake_model,
        api_key=settings.ia_triagem_api_key,
        base_url=settings.ia_triagem_base_url,
        timeout_s=settings.whatsapp_intake_timeout_s,
        max_tokens=_MAX_TOKENS_SAIDA,
        schema=SaidaWhatsAppIntake,
    )
    duracao_ms = int((time.monotonic() - inicio) * 1000)
    if erro:
        log.warning("[WA INTAKE] Conversa %s sem saída útil: %s", conversa_id, erro)

    acao = decidir_acao_intake(saida, rodada, settings.whatsapp_intake_max_rodadas)
    resultado = saida.model_dump() if saida is not None else {"erro": erro}

    if acao == "PERGUNTA":
        assert saida is not None  # garantido por decidir_acao_intake
        await _finalizar(
            conversa_id, telefone, rodada, "PERGUNTA",
            str(saida.pergunta_esclarecimento or "").strip(),
            settings, resultado, tokens_in, tokens_out, duracao_ms,
            novo_status="COLETANDO", pergunta=str(saida.pergunta_esclarecimento or "").strip(),
        )
        return

    if acao == "CRIAR_CHAMADO":
        assert saida is not None
        await _criar_chamado_da_conversa(
            conversa_id, telefone, rodada, perfil, claims, catalogo, saida,
            mensagens_acumuladas, settings, resultado, tokens_in, tokens_out, duracao_ms,
        )
        return

    await _finalizar(
        conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
        TEXTO_ERRO_GENERICO, settings, resultado, tokens_in, tokens_out, duracao_ms,
    )


async def _perfil_por_id(perfil_id: str) -> dict[str, Any] | None:
    async with admin_connection() as conn:
        linha = await conn.fetchrow(
            """
            SELECT p.id::text AS id, p.nome, p.empresa_id::text AS empresa_id,
                   p.departamento_id::text AS departamento_id, d.nome AS departamento_nome
              FROM perfis p
              LEFT JOIN departamentos d ON d.id = p.departamento_id
             WHERE p.id = $1::uuid AND p.ativo = true
            """,
            perfil_id,
        )
    return dict(linha) if linha else None


async def _criar_chamado_da_conversa(
    conversa_id: str,
    telefone: str,
    rodada: int,
    perfil: dict[str, Any],
    claims: dict[str, str],
    catalogo: list[dict[str, Any]],
    saida: SaidaWhatsAppIntake,
    conversa: list[dict[str, Any]],
    settings: Settings,
    resultado: dict[str, Any],
    tokens_in: int | None,
    tokens_out: int | None,
    duracao_ms: int,
) -> None:
    """Cria o chamado em nome do perfil identificado (RLS normal) e confirma."""
    from app.repositories.chamados import ChamadosRepo, validar_telefone_contato

    departamento = _casar(saida.departamento, catalogo)
    if departamento is None:
        log.warning("[WA INTAKE] Departamento fora do catálogo — nada criado.")
        await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                         TEXTO_ERRO_GENERICO, settings, resultado, tokens_in, tokens_out, duracao_ms)
        return
    categoria = _casar(saida.categoria, departamento["categorias"])
    if categoria is None:
        log.warning("[WA INTAKE] Categoria fora do catálogo — nada criado.")
        await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                         TEXTO_ERRO_GENERICO, settings, resultado, tokens_in, tokens_out, duracao_ms)
        return
    subcategoria = _casar(saida.subcategoria, categoria["subcategorias"])

    # `setor` = setor DEMANDANTE (o do próprio autor), mesma semântica do
    # formulário web. Sem departamento no perfil não inventamos valor.
    if not perfil.get("departamento_nome"):
        await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                         TEXTO_SEM_SETOR, settings, resultado, tokens_in, tokens_out, duracao_ms)
        return

    try:
        telefone_contato = validar_telefone_contato(telefone)
    except ValueError:
        telefone_contato = telefone

    titulo = str(saida.titulo or "").strip()[:200]
    repo = ChamadosRepo()
    novo = await repo.criar(
        claims,
        empresa_id=str(perfil["empresa_id"]),
        cliente_id=perfil["id"],
        categoria_id=str(categoria["id"]),
        subcategoria_id=str(subcategoria["id"]) if subcategoria else None,
        departamento_id=str(departamento["id"]),
        titulo=titulo,
        descricao=str(saida.descricao or "").strip(),
        prioridade="MEDIA",
        setor=str(perfil["departamento_nome"]),
        telefone_contato=telefone_contato,
        origem_demanda=_ORIGEM_DEMANDA,
    )
    chamado_id = str(novo["id"])

    await _anexar_imagem(conversa, perfil, claims, chamado_id, repo, settings)
    await _finalizar(
        conversa_id, telefone, rodada, "CHAMADO_CRIADO",
        f"Chamado {novo['codigo']} aberto! A equipe de {departamento['nome']} "
        f"vai te atender em breve. Acompanhe pelo Portal de Chamados.",
        settings, resultado, tokens_in, tokens_out, duracao_ms,
        novo_status="CONCLUIDA", chamado_id=chamado_id,
    )
    await _pos_criacao(
        chamado_id, str(novo["codigo"]), titulo, departamento, perfil, claims, repo
    )


async def _anexar_imagem(
    conversa: list[dict[str, Any]],
    perfil: dict[str, Any],
    claims: dict[str, str],
    chamado_id: str,
    repo: Any,
    settings: Settings,
) -> None:
    """Sobe a foto enviada no WhatsApp como anexo da 1ª mensagem do chamado.

    Upload via ``service_role`` (o usuário não tem sessão HTTP aqui): seguro
    porque o ``path`` é construído 100% pelo servidor a partir de
    ``empresa_id``/``chamado_id`` já validados pela RLS na criação — nada do
    payload do usuário entra nele. Falha nunca derruba o chamado já criado."""
    from app.security.uploads import validar_anexo
    from app.storage import AnexosStorage, ensure_storage
    from app.whatsapp_client import baixar_midia

    midia_id = next((m.get("midia_id") for m in reversed(conversa) if m.get("midia_id")), None)
    if not midia_id or not settings.supabase_service_role_key:
        return
    try:
        baixado = await baixar_midia(str(midia_id))
        if baixado is None:
            return
        conteudo, mime = baixado
        ext = "png" if "png" in mime else "jpg"
        validado = validar_anexo(
            f"whatsapp.{ext}", conteudo, max_bytes=settings.anexo_max_bytes
        )
        storage = await ensure_storage()
        if storage is None:
            return
        path = AnexosStorage.path(str(perfil["empresa_id"]), chamado_id, validado.nome_objeto)
        await storage.upload(
            settings.supabase_service_role_key, path, validado.conteudo, validado.mime
        )
        await repo.adicionar_mensagem(
            claims,
            chamado_id,
            remetente_id=perfil["id"],
            conteudo="",
            anexos=[
                {
                    "path": path,
                    "nome": validado.nome_original,
                    "mime": validado.mime,
                    "tamanho": validado.tamanho,
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 — anexo é opcional (inclui UploadInvalido)
        log.warning("[WA INTAKE] Anexo do WhatsApp falhou (chamado %s): %s", chamado_id, exc)


async def _pos_criacao(
    chamado_id: str,
    codigo: str,
    titulo: str,
    departamento: dict[str, Any],
    perfil: dict[str, Any],
    claims: dict[str, str],
    repo: Any,
) -> None:
    """Mesmas integrações que a abertura pelo Portal dispara: triagem por IA
    (se o departamento estiver habilitado) e aviso por e-mail à equipe. Nunca
    derruba o chamado, que já existe."""
    from app.ia import triagem
    from app.notification import notificar_novo_chamado_email

    settings = get_settings()
    try:
        if triagem.deve_triar(departamento["nome"], settings):
            triagem.agendar_triagem(chamado_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("[WA INTAKE] Falha ao agendar triagem: %s", exc)

    try:
        equipe = await repo.operadores(
            claims, departamento_id=str(departamento["id"]), excluir_id=perfil["id"]
        )
        destinatarios = [str(o["id"]) for o in equipe if o.get("role") == "OPERADOR"]
        if destinatarios:
            await notificar_novo_chamado_email(
                {
                    "id": chamado_id,
                    "codigo": codigo,
                    "titulo": titulo,
                    "departamento_nome": departamento["nome"],
                },
                destinatarios,
            )
    except Exception as exc:  # noqa: BLE001 — e-mail nunca derruba o intake
        log.warning("[WA INTAKE] Falha ao notificar equipe: %s", exc)


async def _finalizar(
    conversa_id: str,
    telefone: str,
    rodada: int,
    acao: str,
    texto_resposta: str,
    settings: Settings,
    resultado: dict[str, Any],
    tokens_in: int | None,
    tokens_out: int | None,
    duracao_ms: int | None,
    *,
    novo_status: str = "CONCLUIDA",
    chamado_id: str | None = None,
    pergunta: str | None = None,
) -> None:
    """Grava auditoria + novo estado da conversa e responde ao usuário.

    A auditoria usa ``ON CONFLICT DO NOTHING`` no ``UNIQUE(conversa_id,
    rodada)``: se a linha já existe, esta rodada já foi processada e resolvida
    por outra execução — não duplicamos a resposta ao usuário."""
    async with admin_connection() as conn:
        auditoria_id = await conn.fetchval(
            """
            INSERT INTO ia_whatsapp_intake
              (conversa_id, rodada, acao, resultado, modelo, tokens_entrada,
               tokens_saida, custo_usd, duracao_ms)
            VALUES ($1::uuid, $2, $3, $4::jsonb, $5, $6, $7, $8, $9)
            ON CONFLICT (conversa_id, rodada) DO NOTHING
            RETURNING id
            """,
            conversa_id,
            rodada,
            acao,
            json.dumps(resultado, ensure_ascii=False, default=str),
            settings.whatsapp_intake_model,
            tokens_in,
            tokens_out,
            cliente.custo_usd(settings.whatsapp_intake_model, tokens_in, tokens_out),
            duracao_ms,
        )
        if auditoria_id is None:
            # A rodada já foi auditada mas a conversa continua em PROCESSANDO:
            # a execução anterior morreu ENTRE o INSERT e o UPDATE abaixo. Sem
            # encerrar aqui, a reconciliação reprocessaria esta mesma rodada a
            # cada varredura, para sempre (o número da rodada nunca avançaria).
            # FALHOU é terminal e libera o índice único parcial de conversa
            # aberta — a próxima mensagem do usuário começa uma conversa nova.
            log.warning(
                "[WA INTAKE] Conversa %s rodada %s já auditada com estado inconsistente — encerrando.",
                conversa_id,
                rodada,
            )
            await conn.execute(
                "UPDATE whatsapp_conversas SET status='FALHOU', atualizada_em=now() "
                "WHERE id=$1::uuid AND status='PROCESSANDO'",
                conversa_id,
            )
            return

        registro_pergunta = (
            json.dumps([{"papel": "assistente", "conteudo": pergunta}], ensure_ascii=False)
            if pergunta
            else "[]"
        )
        await conn.execute(
            """
            UPDATE whatsapp_conversas
               SET status = $2,
                   rodada = $3,
                   chamado_id = COALESCE($4::uuid, chamado_id),
                   mensagens_acumuladas = mensagens_acumuladas || $5::jsonb,
                   atualizada_em = now()
             WHERE id = $1::uuid
            """,
            conversa_id,
            novo_status,
            rodada,
            chamado_id,
            registro_pergunta,
        )
        await conn.execute(
            """
            UPDATE whatsapp_mensagens_recebidas
               SET status='PROCESSADA', processado_em=now()
             WHERE conversa_id = $1::uuid AND status IN ('PENDENTE', 'PROCESSANDO')
            """,
            conversa_id,
        )

    if texto_resposta:
        await _responder(telefone, texto_resposta)


async def _encerrar(conversa_id: str, status: str) -> None:
    async with admin_connection() as conn:
        await conn.execute(
            "UPDATE whatsapp_conversas SET status=$2, atualizada_em=now() WHERE id=$1::uuid",
            conversa_id,
            status,
        )


# --------------------------------------------------------------------------
# Reconciliação (rede de segurança contra restart/redeploy)
# --------------------------------------------------------------------------


async def _conversas_travadas(conn: Any) -> list[str]:
    """Conversas que precisam ser reprocessadas: mensagem gravada que nunca
    saiu de PENDENTE, ou conversa presa em PROCESSANDO além da margem (a task
    morreu no meio — tipicamente um restart)."""
    linhas = await conn.fetch(
        """
        SELECT DISTINCT c.id::text AS id
          FROM whatsapp_conversas c
         WHERE (
                 c.status = 'COLETANDO'
                 AND EXISTS (
                   SELECT 1 FROM whatsapp_mensagens_recebidas m
                    WHERE m.conversa_id = c.id AND m.status = 'PENDENTE'
                      AND m.created_at < now() - $1::interval
                 )
               )
            OR (c.status = 'PROCESSANDO' AND c.atualizada_em < now() - $1::interval)
        """,
        _MARGEM_TRAVADA,
    )
    return [r["id"] for r in linhas]


async def reconciliar_intake_perdido() -> int:
    """Varredura periódica: reprocessa conversas travadas.

    Segura por construção — ``processar_conversa`` revalida tudo no banco e a
    auditoria tem ``UNIQUE(conversa_id, rodada)``, então reprocessar o que já
    foi concluído por outra via é no-op barato. Nunca lança."""
    settings = get_settings()
    if not intake_ativo(settings):
        return 0
    async with admin_connection() as conn:
        # Conversa presa em PROCESSANDO volta a COLETANDO para o lock otimista
        # de `_processar_conversa` conseguir pegá-la de novo.
        ids = await _conversas_travadas(conn)
        if ids:
            await conn.execute(
                """
                UPDATE whatsapp_conversas
                   SET status='COLETANDO'
                 WHERE id = ANY($1::uuid[]) AND status = 'PROCESSANDO'
                """,
                ids,
            )
    for conversa_id in ids:
        await processar_conversa(conversa_id)
    if ids:
        log.warning("[WA INTAKE] Reconciliação reprocessou %d conversa(s).", len(ids))
    return len(ids)


async def _loop_reconciliacao(intervalo_s: float) -> None:
    """Roda :func:`reconciliar_intake_perdido` a cada ``intervalo_s``, até a
    task ser cancelada no shutdown do app."""
    while True:
        await asyncio.sleep(intervalo_s)
        try:
            await reconciliar_intake_perdido()
        except Exception as exc:  # noqa: BLE001 — loop de fundo nunca morre por uma volta
            log.warning("[WA INTAKE] Ciclo de reconciliação falhou: %s", exc)


def iniciar_reconciliacao(settings: Settings) -> asyncio.Task | None:
    """Inicia o loop de reconciliação (chamado pelo lifespan do app).

    ``None`` quando o intake está desligado ou o intervalo é ``<= 0`` — a rede
    de segurança segue o mesmo kill switch geral."""
    if not intake_ativo(settings) or settings.whatsapp_intake_reconciliacao_intervalo_s <= 0:
        return None
    return asyncio.create_task(
        _loop_reconciliacao(settings.whatsapp_intake_reconciliacao_intervalo_s)
    )

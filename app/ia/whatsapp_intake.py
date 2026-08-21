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
import random
import time
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import Settings, get_settings
from app.db import admin_connection
from app.domain.formularios_quimico import (
    CampoDef,
    campos_da_categoria,
    eh_categoria_quimico,
    observacao_categoria,
    titulo_e_descricao_automaticos,
    validar_payload as validar_payload_quimico,
)
from app.domain.formularios_rh import FormularioObrigatorio, formulario_da_subcategoria
from app.domain.periodo import TZ_BR
from app.ia import anexos_contexto, cliente
from app.ia.catalogo_prompt import linha_catalogo
from app.ia.chamada_estruturada import chamar_modelo_estruturado
from app.ia.schemas import SaidaWhatsAppIntake
from app.services.whatsapp_intake_claims import claims_do_perfil

log = logging.getLogger("app.ia_whatsapp_intake")

_MAX_TOKENS_SAIDA = 900
_ORIGEM_DEMANDA = "WhatsApp"

# Rede de segurança contra o modelo repetir uma mensagem já enviada nesta
# conversa (achado em produção, 2026-08-19: mesmo com o estado da conversa
# injetado como fato pronto em `montar_mensagens` — não deduzido pelo modelo
# — o gpt-5.4-mini ainda reincidia quando a última mensagem do usuário era
# curta ou parecia saudação, ex.: "Brigadistas", "Alô". Prompt sozinho não
# bastou; a garantia real é verificar o texto que SAIRIA e, se for repetição,
# nunca mandar — 1 nova tentativa reforçada, e se ainda assim repetir, um
# texto fixo genérico substitui, garantindo que o usuário nunca recebe a
# mesma mensagem duas vezes).
_LEMBRETE_ANTI_REPETICAO = {
    "role": "system",
    "content": (
        "Sua resposta anterior repetiu uma mensagem que você já mandou nesta "
        "mesma conversa (comparei o texto e é idêntico). Isso é proibido. "
        "Releia a ÚLTIMA linha [usuário] do histórico acima e responda "
        "especificamente a ela, com um texto NOVO — nunca repita a "
        "apresentação nem qualquer pergunta já feita, mesmo que a resposta da "
        "pessoa tenha sido curta."
    ),
}
_TEXTO_CONTINUAR_GENERICO = (
    "Só um instante! Pode me contar de novo, rapidinho, o que você precisa? "
    "Quero ter certeza de que anotei certinho."
)
# Margem antes de considerar uma conversa "travada" na reconciliação — evita
# corrida com a task recém-disparada (mesmo papel de `_MARGEM_ORFAO` na
# triagem, onde 1 min já se mostrou suficiente em produção).
_MARGEM_TRAVADA = "2 minutes"

# Mensagens fixas — mesmo tom amigável que o prompt exige do modelo (decisão
# do gestor, 2026-08-18): quem lê não distingue o que veio da IA do que veio
# daqui, então destoar aqui quebra a persona do bot inteira.
TEXTO_SEM_CADASTRO = (
    "Oi! Eu sou o bot de chamados da Bondmann 🙂\n"
    "Não encontrei este número no cadastro do Portal, então ainda não consigo "
    "abrir chamado para você por aqui. É só pedir ao TI para cadastrar o seu "
    "número no seu perfil do Portal que a gente se resolve por aqui na próxima!"
)
TEXTO_ERRO_GENERICO = (
    "Desculpa, deu um problema aqui do meu lado e não consegui registrar seu "
    "chamado agora. Pode tentar de novo daqui a alguns minutos? Se preferir "
    "não esperar, dá para abrir direto pelo Portal de Chamados."
)
TEXTO_SEM_SETOR = (
    "Quase lá! Só que não consegui identificar o seu setor, e ele é "
    "obrigatório para abrir o chamado. Peça ao TI para completar o seu perfil "
    "no Portal, aí a gente resolve por aqui rapidinho."
)


def _texto_fora_de_escopo(settings: Settings, departamentos_disponiveis: list[str]) -> str:
    """Assunto sem destino no catálogo disponível (achado do teste real do
    setor Brigadistas, 2026-08-19 — piloto restrito a poucos departamentos).
    Função (não constante), não só pelo link do portal (mesmo padrão de
    :func:`_texto_confirmacao`) mas porque a lista de setores cobertos muda
    com o rollout (`WHATSAPP_INTAKE_DEPARTAMENTOS`) — nomear fixo aqui
    envelheceria a mensagem a cada novo departamento habilitado."""
    link = f"{settings.portal_base_url.rstrip('/')}/portal/chamados/novo"
    cobertos = " e ".join(departamentos_disponiveis) or "alguns setores"
    return (
        "Entendi o que você precisa, mas esse assunto ainda não está "
        f"disponível pra abrir por aqui pelo WhatsApp — por enquanto só "
        f"cobrimos chamados de {cobertos}. Pode abrir direto pelo Portal que "
        f"a equipe certa vai te atender: {link}"
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
    suporte (áudio, vídeo, localização…) entram com ``corpo`` vazio para o
    modelo pedir o relato por texto, em vez de sumirem em silêncio.
    ``document`` (2026-08-19, pedido do gestor — documento importa até para
    TI, não só foto para o Marketing) extrai ``midia_id`` igual a ``image``,
    mais ``midia_nome`` (o ``filename`` que a Meta manda só para documento —
    é ele que decide a extensão na validação de upload; imagem não tem
    filename, a extensão vem do MIME)."""
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
                midia_nome = None
                if tipo == "text":
                    corpo = str((msg.get("text") or {}).get("body") or "")
                elif tipo == "image":
                    imagem = msg.get("image") or {}
                    midia_id = imagem.get("id")
                    corpo = str(imagem.get("caption") or "")
                elif tipo == "document":
                    documento = msg.get("document") or {}
                    midia_id = documento.get("id")
                    midia_nome = documento.get("filename")
                    corpo = str(documento.get("caption") or "")
                mensagens.append(
                    {
                        "wamid": str(wamid),
                        "telefone": str(de),
                        "tipo": tipo,
                        "corpo": corpo,
                        "midia_id": midia_id,
                        "midia_nome": midia_nome,
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


# Frases que sinalizam "isso é pra um chamado NOVO, não continuação do que
# acabou de abrir" — só importa na legenda de uma foto/documento: sem essa
# saída, `_chamado_recente_para_anexo` sempre anexaria mídia no chamado
# anterior, mesmo quando é evidência de um problema diferente (ex.: pessoa
# manda foto de um problema novo com a legenda "chamado novo: ar-condicionado
# vazando" logo depois de ter aberto outro chamado). Palavra-chave, não IA:
# decisão estrutural barata, não vale chamar modelo só pra isso.
_PEDE_NOVO_CHAMADO = (
    "novo chamado",
    "chamado novo",
    "outro chamado",
    "outro problema",
    "outro assunto",
    "assunto diferente",
    "abrir outro",
)


def _pede_novo_chamado(texto: str) -> bool:
    """``True`` quando a legenda da mídia diz explicitamente que é pra um
    chamado novo (função pura, comparação por substring sem caixa/espaços
    nas pontas — mesmo estilo de :func:`_repete_mensagem_anterior`)."""
    alvo = (texto or "").strip().casefold()
    return any(chave in alvo for chave in _PEDE_NOVO_CHAMADO)


async def _chamado_recente_para_anexo(
    conn: Any, telefone: str, janela_s: float
) -> dict[str, Any] | None:
    """``{chamado_id, codigo}`` do chamado aberto por este telefone há pouco
    tempo, se ainda estiver dentro da janela de anexo pós-criação — ``None``
    quando não há chamado recente o bastante, ou quando há uma conversa
    ATIVA para o telefone (mensagem em andamento tem prioridade: a mídia
    entra no fluxo normal de intake, não vira anexo direto). Uma única
    consulta cobre as duas condições para não ter corrida entre elas."""
    linha = await conn.fetchrow(
        """
        SELECT c.id::text AS chamado_id, c.codigo
          FROM whatsapp_conversas wc
          JOIN chamados c ON c.id = wc.chamado_id
         WHERE wc.telefone = $1
           AND wc.status = 'CONCLUIDA'
           AND wc.chamado_id IS NOT NULL
           AND wc.atualizada_em > now() - ($2 * interval '1 second')
           AND NOT EXISTS (
             SELECT 1 FROM whatsapp_conversas wc2
              WHERE wc2.telefone = $1 AND wc2.status IN ('COLETANDO', 'PROCESSANDO')
           )
         ORDER BY wc.atualizada_em DESC
         LIMIT 1
        """,
        telefone,
        janela_s,
    )
    return dict(linha) if linha else None


async def _receber_mensagem(msg: dict[str, Any]) -> None:
    """Grava a âncora durável da mensagem e agenda a conversa (uma mensagem)."""
    settings = get_settings()
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

        # Foto/documento chegando pouco depois de um chamado ter sido aberto
        # por este mesmo telefone: anexa direto no chamado, sem reabrir o
        # roteiro do intake do zero (2026-08-19, pedido do gestor). Janela
        # <= 0 desliga o recurso (mesmo padrão de `iniciar_reconciliacao`).
        # Legenda explicitando chamado NOVO (`_pede_novo_chamado`) pula o
        # atalho — a pessoa disse que não é continuação, então cai no fluxo
        # normal abaixo e abre uma conversa nova de verdade.
        if (
            msg["midia_id"]
            and msg["tipo"] in ("image", "document")
            and settings.whatsapp_intake_anexo_janela_s > 0
            and not _pede_novo_chamado(msg["corpo"])
        ):
            alvo = await _chamado_recente_para_anexo(
                conn, telefone, settings.whatsapp_intake_anexo_janela_s
            )
            if alvo is not None:
                await conn.execute(
                    "UPDATE whatsapp_mensagens_recebidas SET status='PROCESSADA', processado_em=now() WHERE id=$1",
                    recebida_id,
                )
                _agendar(_anexar_midia_pos_criacao(alvo, perfil, msg, telefone, settings))
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
                        "midia_nome": msg["midia_nome"],
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


async def _responder_documento(telefone: str, link: str, *, nome_arquivo: str) -> None:
    """Envia um documento (o modelo em branco de um formulário obrigatório)
    — nunca lança, mesma tolerância de :func:`_responder`. Falha aqui não
    pode travar a conversa: o texto explicativo mandado logo em seguida ainda
    traz o link, então a pessoa consegue baixar mesmo se o envio do documento
    falhar."""
    from app.whatsapp_client import enviar_documento

    try:
        await enviar_documento(telefone, link, nome_arquivo=nome_arquivo, legenda=nome_arquivo)
    except Exception as exc:  # noqa: BLE001
        log.warning("[WA INTAKE] Falha ao enviar documento no WhatsApp: %s", type(exc).__name__)


# --------------------------------------------------------------------------
# Decisão (pura) e processamento da conversa
# --------------------------------------------------------------------------


# Frases de transição para quando há 2+ perguntas de investigação — escolhidas
# aqui (não pelo modelo) para o formato nunca depender da boa vontade do
# modelo e pra não repetir sempre a mesma introdução (achado da sessão
# 2026-08-19: o modelo tendia a reusar "Oi! Já anotei que..." toda rodada, o
# que soa robótico). Nenhuma tem saudação nem reintroduz o bot — só a rodada 1
# faz isso, e essa lista não entra nela (pergunta única não passa por aqui).
_LEAD_INS_MULTIPLAS_PERGUNTAS = (
    "Pra eu abrir certinho, me diz:",
    "Só mais alguns detalhes pra eu direcionar certo:",
    "Me ajuda com mais uma coisinha:",
    "Pra te atender melhor, preciso saber:",
)


def texto_das_perguntas(perguntas: list[str]) -> str:
    """Junta as perguntas do modelo numa única mensagem de WhatsApp.

    Uma pergunta vai crua — cobre tanto a mensagem de apresentação da primeira
    rodada (que o prompt instrui a mandar como item único) quanto um
    esclarecimento pontual. Duas ou três ganham uma transição curta e
    numeração com quebra de linha real, pra pessoa responder item a item —
    quem garante esse formato é o código, não a redação do modelo (que já
    numerou sozinho, sem quebra de linha, ou embutiu a transição dentro da
    primeira pergunta como texto corrido)."""
    limpas = [p.strip() for p in perguntas if p and p.strip()]
    if not limpas:
        return ""
    if len(limpas) == 1:
        return limpas[0]
    lead_in = random.choice(_LEAD_INS_MULTIPLAS_PERGUNTAS)
    itens = "\n".join(f"{i}. {p}" for i, p in enumerate(limpas, 1))
    return f"{lead_in}\n{itens}"


def _repete_mensagem_anterior(texto: str, conversa: list[dict[str, Any]]) -> bool:
    """``True`` se ``texto`` é idêntico a alguma mensagem que o BOT já mandou
    nesta conversa (função pura — comparação exata, sem caixa/espaços nas
    pontas). Base da rede de segurança contra o modelo travar repetindo a
    apresentação (ver ``_LEMBRETE_ANTI_REPETICAO``)."""
    alvo = texto.strip().casefold()
    if not alvo:
        return False
    return any(
        str(item.get("conteudo") or "").strip().casefold() == alvo
        for item in conversa
        if item.get("papel") != "usuario"
    )


def decidir_acao_intake(
    saida: SaidaWhatsAppIntake | None, rodada: int, max_rodadas: int
) -> str:
    """``CRIAR_CHAMADO`` | ``PERGUNTA`` | ``ENCERRAR_SEM_CHAMADO`` (função pura).

    Sem saída válida do modelo ⇒ encerra (o usuário recebe orientação de usar
    o Portal). Assunto fora do escopo do piloto (nenhum destino do catálogo
    serve, mesmo com o relato entendido) ⇒ encerra ANTES de checar rodada ou
    pergunta — insistir perguntando não vai abrir um destino que não existe
    (achado do teste real do setor Brigadistas, 2026-08-19, com o piloto
    restrito a poucos departamentos). Informação suficiente **e setor
    informado** ⇒ cria: o setor é campo obrigatório do chamado e, por decisão
    do gestor (2026-08-18), vem da conversa, não do cadastro — modelo que diz
    "suficiente" sem setor está errado e vira pergunta, não chamado sem dono
    declarado. Insuficiente ⇒ pergunta, desde que haja pergunta formulada e
    ainda reste rodada; no teto, encerra em vez de perguntar para sempre."""
    if saida is None:
        return "ENCERRAR_SEM_CHAMADO"
    if saida.assunto_fora_do_escopo:
        return "ENCERRAR_SEM_CHAMADO"
    if saida.informacoes_suficientes and (saida.setor or "").strip():
        return "CRIAR_CHAMADO"
    if rodada >= max_rodadas or not texto_das_perguntas(saida.perguntas):
        return "ENCERRAR_SEM_CHAMADO"
    return "PERGUNTA"


def _casar_setor(nome: str | None, setores: list[str]) -> str | None:
    """Casa o setor dito na conversa com um setor ativo real.

    Mesma comparação exata de :func:`_casar` (sem caixa/espaços nas pontas),
    sobre uma lista de strings em vez de linhas de catálogo. Devolve o nome
    CANÔNICO — o que vai para o banco é o do cadastro, nunca a grafia que o
    modelo escreveu."""
    alvo = (nome or "").strip().casefold()
    if not alvo:
        return None
    return next((s for s in setores if s.strip().casefold() == alvo), None)


def _prioridade_valida(prioridade: str | None) -> str:
    """Prioridade do modelo, degradando para ``MEDIA``.

    O schema já restringe os valores, mas a degradação explícita cobre o
    ``None`` (modelo omitiu) sem espalhar a decisão pelo chamador — e mantém o
    comportamento anterior à decisão de 2026-08-18, quando era fixo em MEDIA."""
    from app.repositories.chamados import PRIORIDADES

    candidato = (prioridade or "").strip().upper()
    return candidato if candidato in PRIORIDADES else "MEDIA"


def _link_chamado(chamado_id: str, settings: Settings) -> str:
    """URL pública de acompanhamento do chamado (mesma rota que o portal usa
    no redirect pós-abertura: ``/portal/chamados/{id}``)."""
    return f"{settings.portal_base_url.rstrip('/')}/portal/chamados/{chamado_id}"


# Emoji da confirmação de chamado criado — variado (2026-08-19) pra não usar
# sempre o mesmo em toda conversa; texto fixo em Python não tem como "decidir"
# sozinho, então a escolha aleatória mora aqui.
_EMOJIS_CONFIRMACAO = ("🙂", "😊", "👍", "✅", "😉")


def _texto_confirmacao(codigo: str, departamento_nome: str, chamado_id: str, settings: Settings) -> str:
    emoji = random.choice(_EMOJIS_CONFIRMACAO)
    anexo = (
        "\n\nSe quiser mandar foto ou documento, é só mandar aqui agora que eu "
        "anexo no chamado."
        if settings.whatsapp_intake_anexo_janela_s > 0
        else ""
    )
    return (
        f"Prontinho! Abri o chamado {codigo} para você {emoji}\n"
        f"A equipe de {departamento_nome} já foi avisada e vai te atender.\n\n"
        f"Acompanhe por aqui: {_link_chamado(chamado_id, settings)}{anexo}"
    )


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


async def _setores_validos(claims: dict[str, str]) -> list[str]:
    """Nomes dos setores ativos — domínio do campo obrigatório ``setor``.

    Mesma fonte e mesma semântica do formulário de abertura do portal
    (``app/routes/portal.py``: ``setores`` = todos os departamentos ativos, não
    só os que recebem chamado — o setor aqui é o DEMANDANTE, quem está pedindo,
    e Produção/Comercial não têm fila mas abrem chamado). Validar contra esta
    lista é o que impede o modelo de inventar setor: o POST do portal recusa
    valor fora dela (``portal.py`` linha 483), e aqui a regra é a mesma."""
    from app.repositories.catalogo import CatalogoRepo

    return [
        str(d["nome"]) for d in await CatalogoRepo().departamentos_ativos(claims) if d.get("nome")
    ]


def _saudacao_horario(agora: datetime | None = None) -> str:
    """"Bom dia"/"Boa tarde"/"Boa noite" a partir do relógio real de Brasília.

    O modelo não tem acesso a relógio — pedir pra ele "verificar o horário"
    (decisão do gestor, 2026-08-19) só produziria hora inventada. A conversão
    é feita aqui e injetada pronta em :func:`montar_mensagens`; o prompt
    proíbe o modelo de calcular sozinho. Faixas: 5h–11h59 manhã, 12h–17h59
    tarde, 18h–4h59 noite (mesmo corte de senso comum usado no cumprimento de
    telefone/recepção no Brasil)."""
    hora = (agora or datetime.now(TZ_BR)).hour
    if 5 <= hora < 12:
        return "Bom dia"
    if 12 <= hora < 18:
        return "Boa tarde"
    return "Boa noite"


_ROTULOS_CAMPOS_CONFIRMADOS = {
    "setor": "Setor de quem está pedindo",
    "departamento": "Departamento de destino",
    "categoria": "Categoria",
    "subcategoria": "Subcategoria",
    "data_entrega": "Data de entrega (Marketing)",
    "sem_prazo": "Sem prazo determinado (Marketing)",
}

_NOME_DEPARTAMENTO_QUIMICO = "Dpto Químico"


def _eh_departamento_quimico(nome: str | None) -> bool:
    """Mesma comparação de :meth:`PortalService.quimico_dep_id` (por nome,
    sem caixa/espaços nas pontas), mas sobre o NOME dito na conversa em vez de
    uma lista de departamentos com id — aqui não há id disponível ainda
    quando o prompt precisa decidir se injeta o formulário dinâmico."""
    return (nome or "").strip().casefold() == _NOME_DEPARTAMENTO_QUIMICO.casefold()


def _campo_por_nome(nome_categoria: str, nome_campo: str) -> CampoDef | None:
    return next((c for c in campos_da_categoria(nome_categoria) if c.name == nome_campo), None)


def _linhas_campo_quimico(campo: CampoDef) -> list[str]:
    """Descrição de UM campo do formulário dinâmico do Químico, no formato que
    o modelo recebe no prompt — inclui a lista de opções literal para
    `select`/`checkbox_multi` (é dela que o modelo copia o valor exato; texto
    fora da lista é rejeitado por `formularios_quimico.validar_payload` no
    código, então dar a lista pronta evita ida e volta perguntando de novo)."""
    obrigatorio = "obrigatório" if campo.obrigatorio else "opcional"
    linhas = [f'- `{campo.name}` — "{campo.label}" ({obrigatorio})']
    if campo.ajuda:
        linhas.append(f"  Contexto: {campo.ajuda}")
    if campo.tipo == "select":
        linhas.append(
            "  Tipo: escolha única — quando a pessoa responder, identifique a opção "
            "que ela quis dizer (mesmo com grafia diferente, abreviação ou cidade "
            "em vez do código) e copie EXATAMENTE uma destas opções, nunca um texto "
            "livre. Se nenhuma opção corresponder com clareza, pergunte de novo "
            "mostrando as 2-3 mais prováveis:"
        )
        linhas.append("  " + " | ".join(campo.opcoes))
    elif campo.tipo == "checkbox_multi":
        linhas.append(
            "  Tipo: múltipla escolha — ao chegar a vez deste campo, apresente TODAS "
            "as opções abaixo numeradas em uma única pergunta e peça para a pessoa "
            "responder com os números e/ou os nomes dos itens que quer (pode ser mais "
            "de um). Na resposta dela, mapeie cada item citado para o texto EXATO da "
            "lista abaixo e preencha uma LISTA com todos eles em `campos_formulario` — "
            "nunca invente item fora desta lista, e nunca deixe de perguntar por já "
            "achar que sabe a resposta:"
        )
        linhas += [f"  {i}. {opcao}" for i, opcao in enumerate(campo.opcoes, 1)]
    elif campo.tipo == "date":
        linhas.append("  Tipo: data — converta a resposta para o formato AAAA-MM-DD.")
    elif campo.tipo in ("tel", "email", "number"):
        linhas.append(f"  Tipo: {campo.tipo}.")
    return linhas


def _secao_formulario_quimico(
    nome_categoria: str, campos_formulario_confirmados: dict[str, Any]
) -> list[str]:
    """Bloco injetado no turno `user` quando departamento e categoria do
    Químico já estão confirmados (via `campos_confirmados`, mesmo fato pronto
    do resto da conversa) — descreve campo a campo do layout real da
    categoria (`formularios_quimico.CAMPOS_POR_CATEGORIA`, fonte única
    também usada pelo Portal) para o modelo perguntar UM DE CADA VEZ, na
    ordem do schema, pulando o que já foi confirmado em rodada anterior.
    Categoria do Químico sem layout dinâmico conhecido (ex.: uma futura
    categoria "Outros") devolve lista vazia — a conversa segue o roteiro
    genérico de título/descrição, igual aos demais departamentos."""
    campos = campos_da_categoria(nome_categoria)
    if not campos:
        return []
    linhas = [
        "",
        f'## Formulário do Departamento Químico — categoria "{nome_categoria}"',
        "Esta categoria tem um formulário fixo: em vez do roteiro genérico de "
        "investigação, colete EXATAMENTE os campos abaixo, respeitando o tipo de "
        "cada um. Pergunte só UM campo por rodada (nunca junte dois campos numa "
        "mesma pergunta, mesmo que pareçam relacionados) — exceto o de múltipla "
        "escolha, que já é uma pergunta única com várias opções. Pule qualquer "
        'campo listado em "já confirmados" abaixo — perguntar de novo o que já foi '
        "respondido é o mesmo erro grave de repetir a pergunta do setor. "
        "`informacoes_suficientes` só pode ser `true` quando TODOS os campos "
        "marcados (obrigatório) abaixo estiverem preenchidos em `campos_formulario` "
        "(os opcionais podem ficar de fora). Grave cada valor coletado em "
        "`campos_formulario` usando EXATAMENTE o `name` indicado como chave — "
        "`titulo`/`descricao` não precisam ser preenchidos por você nesta "
        "categoria, o sistema os deriva automaticamente do formulário.",
    ]
    pendentes = [c for c in campos if c.name not in campos_formulario_confirmados]
    for campo in pendentes:
        linhas += _linhas_campo_quimico(campo)
    if campos_formulario_confirmados:
        linhas += ["", "### Campos deste formulário já confirmados (não pergunte de novo)"]
        for nome_campo, valor in campos_formulario_confirmados.items():
            campo_def = _campo_por_nome(nome_categoria, nome_campo)
            rotulo = campo_def.label if campo_def else nome_campo
            texto_valor = "; ".join(valor) if isinstance(valor, list) else str(valor)
            linhas.append(f"- {rotulo}: {texto_valor}")
    return linhas


def montar_mensagens(
    conversa: list[dict[str, Any]],
    catalogo: list[dict[str, Any]],
    imagem_data_uri: str | None = None,
    setores: list[str] | None = None,
    campos_confirmados: dict[str, Any] | None = None,
    campos_formulario_confirmados: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Mensagens system+user da extração (mesmo formato do motor de triagem).

    O catálogo e a lista de setores entram no turno do usuário porque mudam por
    departamento/público; o prompt (system) é fixo e versionado.
    ``campos_confirmados`` (2026-08-20) são setor/departamento/categoria/
    subcategoria/data_entrega/sem_prazo já extraídos em rodada anterior desta
    MESMA conversa (:func:`_campos_confirmados`) — mesmo motivo de
    ``ja_apresentou`` abaixo: um modelo pequeno não é confiável re-derivando
    do histórico de texto o que já respondeu estruturadamente antes. Achado em
    produção (chamado de Marketing, 2026-08-20): `setor` saía certo em
    `saida.setor` desde a rodada 2, mas o modelo mesmo assim reformulou "qual
    é o seu setor?" nas rodadas 2, 3 e 4 — cada reformulação com texto
    diferente escapava do `_repete_mensagem_anterior` (que só pega repetição
    EXATA), queimando rodadas do teto à toa."""
    # Calculado aqui, não deduzido pelo modelo: achado da sessão 2026-08-19,
    # onde o gpt-5.4-mini (pedido pra "ver se já tem linha [assistente] no
    # histórico") continuou repetindo a apresentação idêntica rodada após
    # rodada mesmo com a regra escrita no prompt — inferir estado da conversa
    # a partir do transcript é pedir demais de um modelo pequeno. Um fato
    # pronto, calculado em Python, é muito mais confiável do que uma dedução.
    ja_apresentou = any(item.get("papel") != "usuario" for item in conversa)
    linhas = [
        "## Saudação atual (use EXATAMENTE esta na mensagem de apresentação — "
        "não calcule você mesmo, o relógio é este)",
        _saudacao_horario(),
        "",
        "## Estado da conversa (fato, não precisa deduzir do histórico)",
        (
            "Você JÁ mandou a mensagem de apresentação nesta conversa (está no "
            "histórico abaixo). Está PROIBIDO repetir a apresentação, a "
            "saudação, ou qualquer variação dela nesta resposta — mesmo que "
            "pareça a opção mais segura. Responda especificamente à ÚLTIMA "
            "linha [usuário] do histórico, seguindo a seção 'Nas mensagens "
            "seguintes' do prompt."
            if ja_apresentou
            else "Esta é a primeira mensagem desta conversa — siga a seção "
            "'Primeira mensagem da conversa' do prompt."
        ),
    ]
    if campos_confirmados:
        linhas += [
            "",
            "## Dados já confirmados nesta conversa (fatos — NÃO pergunte de "
            "novo sobre eles, mesmo que o histórico abaixo pareça incompleto "
            "ou você não tenha certeza; copie estes valores exatamente nos "
            "campos correspondentes do JSON desta rodada)",
        ]
        linhas += [
            f"- {_ROTULOS_CAMPOS_CONFIRMADOS.get(campo, campo)}: {valor}"
            for campo, valor in campos_confirmados.items()
        ]
    linhas += [
        "",
        "## Conversa no WhatsApp",
    ]
    for item in conversa:
        papel = "usuário" if item.get("papel") == "usuario" else "assistente"
        conteudo = str(item.get("conteudo") or "").strip()
        if conteudo:
            linhas.append(f"[{papel}] {conteudo}")
        elif item.get("midia_id"):
            linhas.append(f"[{papel}] (enviou uma imagem)")
    if setores:
        linhas += [
            "",
            "## Setores (para o campo `setor` — o setor de quem está pedindo)",
            ", ".join(setores),
        ]
    if catalogo:
        linhas += ["", "## Catálogo disponível"]
        for dep in catalogo:
            linhas.append(f"### Departamento: {dep['nome']}")
            linhas += [linha_catalogo(cat) for cat in dep["categorias"]]

    from app.services.portal import PortalService

    if PortalService.marketing_dep_id(catalogo):
        # Data mínima calculada aqui, não pelo modelo (mesmo motivo da
        # saudação por horário acima): 48h úteis contando fim de semana é
        # conta que um modelo pequeno erra. O código revalida de qualquer
        # forma em `_criar_chamado_da_conversa`, mas dar o valor certo de
        # cara evita rodadas extras pedindo a data de novo.
        hoje = datetime.now(TZ_BR).date()
        minimo = PortalService.data_entrega_min()
        linhas += [
            "",
            "## Prazo do Marketing (só quando o destino for o departamento Marketing)",
            f"Hoje é {hoje.strftime('%d/%m/%Y')}. Se o destino for Marketing, é "
            "obrigatório perguntar quando a pessoa precisa do material pronto antes "
            "de considerar `informacoes_suficientes: true`. Aceite uma data (escreva "
            f"em `data_entrega` no formato AAAA-MM-DD; a mínima aceita é "
            f"{minimo.strftime('%d/%m/%Y')}, e não pode cair em sábado/domingo) ou, "
            "se a pessoa disser que não tem pressa/prazo, marque `sem_prazo: true` e "
            "deixe `data_entrega` vazio.",
        ]

    if campos_confirmados and _eh_departamento_quimico(campos_confirmados.get("departamento")):
        categoria_confirmada = str(campos_confirmados.get("categoria") or "")
        if eh_categoria_quimico(categoria_confirmada):
            linhas += _secao_formulario_quimico(
                categoria_confirmada, campos_formulario_confirmados or {}
            )

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
    opcional, nunca motivo para travar o intake. Documento (2026-08-19) não
    entra aqui — a Meta só manda ``filename`` (``midia_nome``) pra documento,
    nunca pra foto, então isso funciona como filtro sem precisar guardar o
    tipo original da mensagem; mandar um PDF pro bloco `image_url` do modelo
    não faria sentido mesmo (a extração de anexo é só pra ANEXAR, não pra ler)."""
    from app.whatsapp_client import baixar_midia

    midia_id = next(
        (
            m.get("midia_id")
            for m in reversed(conversa)
            if m.get("midia_id") and not m.get("midia_nome")
        ),
        None,
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


_CAMPOS_STICKY = ("setor", "departamento", "categoria", "subcategoria", "data_entrega", "sem_prazo")


async def _resultado_rodada_anterior(conn: Any, conversa_id: str) -> dict[str, Any] | None:
    """``resultado`` (JSON) da rodada mais recente já auditada desta conversa
    (``ia_whatsapp_intake.resultado``, gravado a cada rodada por
    :func:`_finalizar`) — ``None`` na primeira rodada, quando nada foi
    extraído ainda. Fonte única para as duas derivações de "fato pronto" que
    a rodada seguinte recebe (:func:`_campos_confirmados` e
    :func:`_campos_formulario_confirmados`) — uma consulta só, não duas."""
    linha = await conn.fetchrow(
        """
        SELECT resultado FROM ia_whatsapp_intake
         WHERE conversa_id = $1::uuid
         ORDER BY rodada DESC
         LIMIT 1
        """,
        conversa_id,
    )
    if linha is None:
        return None
    resultado = linha["resultado"]
    if isinstance(resultado, str):
        resultado = json.loads(resultado)
    return resultado if isinstance(resultado, dict) else None


def _campos_confirmados(resultado: dict[str, Any] | None) -> dict[str, Any]:
    """Setor/departamento/categoria/subcategoria/data_entrega/sem_prazo já
    extraídos em rodada anterior, a partir do ``resultado`` de
    :func:`_resultado_rodada_anterior` — vazio quando não há nenhum campo
    confirmado ainda."""
    if not resultado:
        return {}
    return {
        campo: resultado[campo]
        for campo in _CAMPOS_STICKY
        if resultado.get(campo) not in (None, "", False)
    }


def _campos_formulario_confirmados(resultado: dict[str, Any] | None) -> dict[str, Any]:
    """Campos do formulário dinâmico do Químico (``campos_formulario``) já
    extraídos em rodada anterior — mesma fonte e mesmo motivo de
    :func:`_campos_confirmados`, só que sobre um dict aninhado em vez de
    chaves escalares fixas (o conjunto de campos muda por categoria)."""
    if not resultado:
        return {}
    campos = resultado.get("campos_formulario")
    if not isinstance(campos, dict):
        return {}
    return {chave: valor for chave, valor in campos.items() if valor not in (None, "", [])}


def _mesclar_campos_confirmados(
    saida: SaidaWhatsAppIntake, confirmados: dict[str, Any]
) -> SaidaWhatsAppIntake:
    """Repõe um campo já confirmado em rodada anterior que o modelo tenha
    esquecido de repetir nesta rodada — nunca deixa um dado real regredir
    pra vazio. Rede de segurança: a correção principal é a injeção do fato
    em :func:`montar_mensagens`; isso cobre o caso do modelo ainda assim
    omitir o campo apesar da instrução."""
    dados = saida.model_dump()
    mudou = False
    for campo, valor in confirmados.items():
        if not dados.get(campo):
            dados[campo] = valor
            mudou = True
    return SaidaWhatsAppIntake(**dados) if mudou else saida


def _mesclar_campos_formulario(
    saida: SaidaWhatsAppIntake, confirmados: dict[str, Any]
) -> SaidaWhatsAppIntake:
    """Mesma rede de segurança de :func:`_mesclar_campos_confirmados`, mas
    para ``campos_formulario`` (dict aninhado do formulário dinâmico do
    Químico): repõe uma chave já confirmada que o modelo tenha omitido nesta
    rodada, sem sobrescrever um valor novo que ele tenha trazido."""
    if not confirmados:
        return saida
    atual = dict(saida.campos_formulario or {})
    mudou = False
    for campo, valor in confirmados.items():
        if not atual.get(campo):
            atual[campo] = valor
            mudou = True
    if not mudou:
        return saida
    dados = saida.model_dump()
    dados["campos_formulario"] = atual
    return SaidaWhatsAppIntake(**dados)


def _aplicar_campos_confirmados(
    saida: SaidaWhatsAppIntake | None,
    campos_confirmados: dict[str, Any],
    campos_formulario_confirmados: dict[str, Any],
) -> SaidaWhatsAppIntake | None:
    """Aplica as duas redes de segurança de "campo já confirmado não se
    perde" (escalar + formulário dinâmico do Químico) numa chamada só —
    reduz a duplicação nos dois pontos que precisam disso (chamada normal e
    retry anti-repetição)."""
    if saida is None:
        return None
    if campos_confirmados:
        saida = _mesclar_campos_confirmados(saida, campos_confirmados)
    if campos_formulario_confirmados:
        saida = _mesclar_campos_formulario(saida, campos_formulario_confirmados)
    return saida


def _campos_formulario_brutos(valores: dict[str, Any] | None) -> dict[str, list[str]]:
    """Converte ``SaidaWhatsAppIntake.campos_formulario`` (``str``/``list[str]``
    por chave, como o modelo devolve) para o formato bruto que
    ``formularios_quimico.validar_payload`` espera (``list[str]`` por chave —
    mesmo formato do multipart do Portal, onde ``checkbox_multi`` chega como
    várias entradas do mesmo campo). Valores vazios/ausentes não entram —
    ``validar_payload`` trata ausência como campo não preenchido."""
    brutos: dict[str, list[str]] = {}
    for nome, valor in (valores or {}).items():
        if isinstance(valor, list):
            limpos = [str(v).strip() for v in valor if str(v).strip()]
            if limpos:
                brutos[nome] = limpos
        elif str(valor or "").strip():
            brutos[nome] = [str(valor).strip()]
    return brutos


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

    campos_confirmados: dict[str, Any] = {}
    campos_formulario_confirmados: dict[str, Any] = {}
    if rodada > 1:
        async with admin_connection() as conn:
            resultado_anterior = await _resultado_rodada_anterior(conn, conversa_id)
        campos_confirmados = _campos_confirmados(resultado_anterior)
        campos_formulario_confirmados = _campos_formulario_confirmados(resultado_anterior)

    catalogo = await _montar_catalogo(claims)
    if not catalogo:
        log.warning("[WA INTAKE] Nenhum departamento habilitado tem catálogo visível.")
        await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                         TEXTO_ERRO_GENERICO, settings, {}, None, None, None)
        return

    setores = await _setores_validos(claims)
    imagem = await _imagem_da_conversa(mensagens_acumuladas, settings)
    mensagens = montar_mensagens(
        mensagens_acumuladas, catalogo, imagem, setores,
        campos_confirmados, campos_formulario_confirmados,
    )
    inicio = time.monotonic()
    saida, erro, tokens_in, tokens_out = await chamar_modelo_estruturado(
        mensagens,
        model=settings.whatsapp_intake_model,
        api_key=settings.ia_triagem_api_key,
        base_url=settings.ia_triagem_base_url,
        timeout_s=settings.whatsapp_intake_timeout_s,
        max_tokens=_MAX_TOKENS_SAIDA,
        schema=SaidaWhatsAppIntake,
    )
    if erro:
        log.warning("[WA INTAKE] Conversa %s sem saída útil: %s", conversa_id, erro)
    saida = _aplicar_campos_confirmados(saida, campos_confirmados, campos_formulario_confirmados)

    acao = decidir_acao_intake(saida, rodada, settings.whatsapp_intake_max_rodadas)
    pergunta = (
        texto_das_perguntas(saida.perguntas) if saida is not None and acao == "PERGUNTA" else ""
    )
    repetiu = False

    if acao == "PERGUNTA" and _repete_mensagem_anterior(pergunta, mensagens_acumuladas):
        # Achado em produção (2026-08-19): mesmo com o estado da conversa
        # injetado como fato, o modelo às vezes trava repetindo a
        # apresentação — nunca manda isso ao usuário; tenta de novo com
        # reforço explícito antes de desistir (ver constantes no topo).
        log.warning(
            "[WA INTAKE] Conversa %s rodada %s: resposta repetida, tentando de novo.",
            conversa_id, rodada,
        )
        repetiu = True
        saida_retry, _erro_retry, tokens_in_retry, tokens_out_retry = await chamar_modelo_estruturado(
            [*mensagens, _LEMBRETE_ANTI_REPETICAO],
            model=settings.whatsapp_intake_model,
            api_key=settings.ia_triagem_api_key,
            base_url=settings.ia_triagem_base_url,
            timeout_s=settings.whatsapp_intake_timeout_s,
            max_tokens=_MAX_TOKENS_SAIDA,
            schema=SaidaWhatsAppIntake,
        )
        tokens_in = tokens_in if tokens_in_retry is None else (tokens_in or 0) + tokens_in_retry
        tokens_out = tokens_out if tokens_out_retry is None else (tokens_out or 0) + tokens_out_retry
        saida_retry = _aplicar_campos_confirmados(
            saida_retry, campos_confirmados, campos_formulario_confirmados
        )
        acao_retry = decidir_acao_intake(saida_retry, rodada, settings.whatsapp_intake_max_rodadas)
        pergunta_retry = (
            texto_das_perguntas(saida_retry.perguntas)
            if saida_retry is not None and acao_retry == "PERGUNTA"
            else ""
        )
        if saida_retry is not None and (
            acao_retry != "PERGUNTA"
            or not _repete_mensagem_anterior(pergunta_retry, mensagens_acumuladas)
        ):
            # Tentativa nova resolveu (ou decidiu criar chamado/encerrar) — usa ela.
            saida, acao, pergunta = saida_retry, acao_retry, pergunta_retry
        else:
            # Repetiu de novo (ou a tentativa falhou): mantém o `setor`/demais
            # campos já extraídos por `saida` (a rede de segurança troca só o
            # TEXTO enviado, nunca descarta dado real já capturado), mas o
            # usuário nunca recebe a mesma mensagem duas vezes.
            pergunta = _TEXTO_CONTINUAR_GENERICO

    duracao_ms = int((time.monotonic() - inicio) * 1000)
    resultado = saida.model_dump() if saida is not None else {"erro": erro}
    if repetiu:
        resultado["retry_anti_repeticao"] = True

    if acao == "PERGUNTA":
        assert saida is not None  # garantido por decidir_acao_intake
        await _finalizar(
            conversa_id, telefone, rodada, "PERGUNTA", pergunta,
            settings, resultado, tokens_in, tokens_out, duracao_ms,
            novo_status="COLETANDO", pergunta=pergunta,
        )
        return

    if acao == "CRIAR_CHAMADO":
        assert saida is not None
        await _criar_chamado_da_conversa(
            conversa_id, telefone, rodada, perfil, claims, catalogo, setores, saida,
            mensagens_acumuladas, settings, resultado, tokens_in, tokens_out, duracao_ms,
        )
        return

    fora_de_escopo = saida is not None and saida.assunto_fora_do_escopo
    await _finalizar(
        conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
        _texto_fora_de_escopo(settings, settings.whatsapp_intake_departamentos_lista)
        if fora_de_escopo
        else TEXTO_ERRO_GENERICO,
        settings, resultado, tokens_in, tokens_out, duracao_ms,
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


async def _formulario_rh_pendente(
    departamento: dict[str, Any],
    subcategoria: dict[str, Any] | None,
    conversa: list[dict[str, Any]],
    settings: Settings,
) -> FormularioObrigatorio | None:
    """O formulário obrigatório da subcategoria quando ela exige um anexo que
    a conversa ainda não tem — ``None`` quando não há exigência (fora do RH,
    ou subcategoria sem formulário) ou o anexo já está presente. Mesma regra
    do Portal (``app/routes/portal.py``, endurecida em 2026-08-10 depois de um
    chamado real ter aberto sem o FB porque "avisar e deixar concluir depois"
    não bastou) — reaproveita
    :func:`app.domain.formularios_rh.formulario_da_subcategoria`, fonte única
    também usada pelo Portal e pelo Workspace, sem reimplementar a lista de
    subcategorias aqui. O WhatsApp segue a mesma trava NA ABERTURA para não
    virar uma porta lateral que reabre o gap que o Portal já fechou."""
    if (departamento.get("nome") or "").strip().casefold() != "rh":
        return None
    formulario = formulario_da_subcategoria(subcategoria["nome"] if subcategoria else None)
    if formulario is None:
        return None

    # Só a ÚLTIMA mensagem da conversa conta como "o anexo chegou" — nunca
    # qualquer mídia em qualquer ponto do histórico. Achado real em produção
    # (2026-08-20, chamado BOND-2026-00780): a conversa tinha um anexo antigo
    # e sem relação nenhuma (mandado antes mesmo de a pessoa dizer o que
    # precisava) logo na primeira mensagem; com a busca "mídia mais recente
    # em qualquer lugar do histórico" esse arquivo satisfazia a exigência do
    # FB030 sem a pessoa nunca ter mandado o formulário de verdade. Restringir
    # à última mensagem exige que o anexo tenha vindo DEPOIS do pedido — no
    # pior caso (a pessoa manda o anexo e, antes da próxima rodada rodar,
    # ainda escreve outra mensagem de texto) o bot só pede de novo, o que é
    # seguro; o que não pode acontecer é o oposto (aceitar anexo errado).
    ultima = conversa[-1] if conversa else None
    if ultima is not None and ultima.get("midia_id"):
        try:
            validado = await _midia_valida(ultima, settings)
        except Exception:  # noqa: BLE001 — inclui UploadInvalido: tipo/tamanho recusado
            validado = None
        if validado is not None:
            return None

    return formulario


def _texto_formulario_pendente(formulario: FormularioObrigatorio) -> str:
    return (
        f'Essa subcategoria ("{formulario.label}") exige o formulário preenchido e '
        "assinado já na abertura do chamado — mandei o modelo aqui em cima "
        "pra você baixar. Preenche, assina e me manda de volta a foto ou o "
        "arquivo que eu sigo com a abertura do chamado."
    )


async def _enviar_formulario_pendente(
    telefone: str, formulario: FormularioObrigatorio, settings: Settings
) -> str:
    """Manda o(s) arquivo(s)-modelo do formulário obrigatório como documento
    de verdade no WhatsApp — não só um link solto em texto (pedido do gestor,
    2026-08-20: quem só vê um link no meio de uma conversa tende a não abrir;
    o documento chega pronto pra visualizar/baixar direto no WhatsApp). Usa
    ``enviar_documento`` com o link público do estático
    (``app/static/formularios/rh/``), sem subir o arquivo de novo a cada
    envio. Devolve o texto gravado no histórico da conversa (a explicação do
    que fazer, mandada por último, depois do(s) documento(s))."""
    base = settings.portal_base_url.rstrip("/")
    for arquivo in formulario.arquivos:
        await _responder_documento(telefone, f"{base}{arquivo.url}", nome_arquivo=arquivo.nome)
    texto = _texto_formulario_pendente(formulario)
    await _responder(telefone, texto)
    return texto


@dataclass(frozen=True)
class _FormularioQuimico:
    """Resultado da validação do formulário dinâmico do Químico para a
    categoria escolhida — mesmo papel de ``RegrasMarketing``
    (``app/services/portal.py``): ``erro`` não fatal vira nova pergunta,
    nunca aborta a conversa."""

    dados_formulario: dict[str, Any]
    titulo: str
    descricao: str
    observacao: str
    erro: str | None = None


_SEM_FORMULARIO_QUIMICO = _FormularioQuimico(dados_formulario={}, titulo="", descricao="", observacao="")


def _validar_formulario_quimico(
    departamento: dict[str, Any], categoria: dict[str, Any], saida: SaidaWhatsAppIntake
) -> _FormularioQuimico:
    """Valida ``saida.campos_formulario`` contra o layout real da categoria do
    Químico, reaproveitando ``formularios_quimico.validar_payload`` — a MESMA
    função que o Portal usa (fonte única, mesmo motivo do
    :func:`_formulario_rh_pendente`). Fora do Químico, ou numa categoria do
    Químico sem layout dinâmico conhecido (``validar_payload`` devolve
    ``limpo`` vazio sem erro), não há o que validar — devolve
    :data:`_SEM_FORMULARIO_QUIMICO` e o resto do fluxo segue com
    `titulo`/`descricao` normais, como qualquer outro departamento."""
    if not _eh_departamento_quimico(str(departamento.get("nome") or "")):
        return _SEM_FORMULARIO_QUIMICO
    nome_categoria = str(categoria.get("nome") or "")
    brutos = _campos_formulario_brutos(saida.campos_formulario)
    ok, erro, limpo = validar_payload_quimico(nome_categoria, brutos)
    if not ok:
        return _FormularioQuimico(dados_formulario={}, titulo="", descricao="", observacao="", erro=erro)
    if not limpo:
        return _SEM_FORMULARIO_QUIMICO
    titulo, descricao = titulo_e_descricao_automaticos(nome_categoria, limpo)
    return _FormularioQuimico(
        dados_formulario=limpo,
        titulo=titulo,
        descricao=descricao,
        observacao=observacao_categoria(nome_categoria),
    )


async def _criar_chamado_da_conversa(
    conversa_id: str,
    telefone: str,
    rodada: int,
    perfil: dict[str, Any],
    claims: dict[str, str],
    catalogo: list[dict[str, Any]],
    setores: list[str],
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

    formulario_pendente = await _formulario_rh_pendente(departamento, subcategoria, conversa, settings)
    if formulario_pendente:
        if rodada >= settings.whatsapp_intake_max_rodadas:
            await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                             TEXTO_ERRO_GENERICO, settings, resultado, tokens_in, tokens_out, duracao_ms)
            return
        # O(s) documento(s) e o texto já saem daqui (`_enviar_formulario_pendente`,
        # que manda o arquivo de verdade, não só um link) — `_finalizar` só
        # grava a auditoria e o histórico, sem mandar mensagem de novo.
        texto_pedido = await _enviar_formulario_pendente(telefone, formulario_pendente, settings)
        await _finalizar(
            conversa_id, telefone, rodada, "PERGUNTA", "",
            settings, resultado, tokens_in, tokens_out, duracao_ms,
            novo_status="COLETANDO", pergunta=texto_pedido,
        )
        return

    # Formulário dinâmico do Departamento Químico (2026-08-21, mesma regra
    # própria de `app/domain/formularios_quimico.py`, já usada pelo Portal):
    # cada categoria tem um layout fixo de campos — a coleta acontece campo a
    # campo ao longo da conversa (`_secao_formulario_quimico`, injetada em
    # `montar_mensagens`), mas quem GARANTE que nada obrigatório ficou de fora
    # é este código, não a boa vontade do modelo (mesmo espírito do
    # `regra.erro` do Marketing logo abaixo). `erro` não é fatal: vira nova
    # pergunta com o texto pronto de `validar_payload` (ex.: 'Preencha o campo
    # "Região".'), respeitando o mesmo teto de rodadas do resto da conversa.
    formulario_quimico = _validar_formulario_quimico(departamento, categoria, saida)
    if formulario_quimico.erro:
        if rodada >= settings.whatsapp_intake_max_rodadas:
            await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                             TEXTO_ERRO_GENERICO, settings, resultado, tokens_in, tokens_out, duracao_ms)
            return
        await _finalizar(
            conversa_id, telefone, rodada, "PERGUNTA", formulario_quimico.erro,
            settings, resultado, tokens_in, tokens_out, duracao_ms,
            novo_status="COLETANDO", pergunta=formulario_quimico.erro,
        )
        return

    # `setor` = setor DEMANDANTE (o do próprio autor), mesma semântica do
    # formulário web. Decisão do gestor (2026-08-18): vem da CONVERSA, não do
    # cadastro — o bot pergunta na apresentação. O cadastro entra só como rede
    # de segurança quando o nome dito não casa com nenhum setor ativo, e aí a
    # regra antiga vale: sem setor de lugar nenhum, não inventamos valor.
    setor = _casar_setor(saida.setor, setores) or perfil.get("departamento_nome")
    if not setor:
        await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                         TEXTO_SEM_SETOR, settings, resultado, tokens_in, tokens_out, duracao_ms)
        return

    prioridade = _prioridade_valida(saida.prioridade)
    data_entrega: date | None = None
    sem_prazo = False
    from app.services.portal import PortalService

    if PortalService.marketing_dep_id(catalogo) == str(departamento["id"]):
        # Fluxo por demanda do Marketing (mesma regra do formulário do
        # Portal): reaproveita `regras_marketing` em vez de reimplementar a
        # validação de data mínima/fim de semana aqui — fonte única também
        # para o WhatsApp. `erro` não é fatal: volta como pergunta nova (o
        # modelo pode ter convertido a data errado, ou a pessoa ainda não
        # respondeu isso), respeitando o mesmo teto de rodadas do resto da
        # conversa para não perguntar para sempre.
        regra = PortalService.regras_marketing(
            departamento_id=str(departamento["id"]),
            setores_ativos=catalogo,
            prioridade=prioridade,
            sem_prazo_marcado=bool(saida.sem_prazo),
            data_entrega=str(saida.data_entrega or "").strip(),
        )
        if regra.erro:
            if rodada >= settings.whatsapp_intake_max_rodadas:
                await _finalizar(conversa_id, telefone, rodada, "ENCERRADO_SEM_CHAMADO",
                                 TEXTO_ERRO_GENERICO, settings, resultado, tokens_in, tokens_out, duracao_ms)
                return
            await _finalizar(
                conversa_id, telefone, rodada, "PERGUNTA", regra.erro,
                settings, resultado, tokens_in, tokens_out, duracao_ms,
                novo_status="COLETANDO", pergunta=regra.erro,
            )
            return
        prioridade, data_entrega, sem_prazo = regra.prioridade, regra.data_entrega, regra.sem_prazo

    try:
        telefone_contato = validar_telefone_contato(telefone)
    except ValueError:
        telefone_contato = telefone

    # Categoria do Químico com layout dinâmico: título e descrição são
    # DERIVADOS do formulário (mesma regra da tela de abertura do Portal, que
    # esconde os dois campos para essas categorias) — nunca os que o modelo
    # tenha preenchido por conta própria.
    titulo = (formulario_quimico.titulo or str(saida.titulo or "").strip())[:200]
    descricao = formulario_quimico.descricao or str(saida.descricao or "").strip()
    repo = ChamadosRepo()
    novo = await repo.criar(
        claims,
        empresa_id=str(perfil["empresa_id"]),
        cliente_id=perfil["id"],
        categoria_id=str(categoria["id"]),
        subcategoria_id=str(subcategoria["id"]) if subcategoria else None,
        departamento_id=str(departamento["id"]),
        titulo=titulo,
        descricao=descricao,
        prioridade=prioridade,
        setor=str(setor),
        telefone_contato=telefone_contato,
        data_entrega=data_entrega,
        sem_prazo=sem_prazo,
        origem_demanda=_ORIGEM_DEMANDA,
        dados_formulario=formulario_quimico.dados_formulario,
    )
    chamado_id = str(novo["id"])

    await _anexar_midia_da_conversa(conversa, perfil, claims, chamado_id, repo, settings)
    texto_confirmacao = _texto_confirmacao(str(novo["codigo"]), str(departamento["nome"]), chamado_id, settings)
    if formulario_quimico.observacao:
        texto_confirmacao = f"{texto_confirmacao}\n\n{formulario_quimico.observacao}"
    await _finalizar(
        conversa_id, telefone, rodada, "CHAMADO_CRIADO",
        texto_confirmacao,
        settings, resultado, tokens_in, tokens_out, duracao_ms,
        novo_status="CONCLUIDA", chamado_id=chamado_id,
    )
    await _pos_criacao(
        chamado_id, str(novo["codigo"]), titulo, departamento, perfil, claims, repo
    )


async def _midia_valida(msg: dict[str, Any], settings: Settings) -> Any:
    """Baixa a mídia (foto ou documento) referenciada em ``msg`` e valida
    como anexo de chamado — mesma allow-list e mesma checagem de MIME real
    do upload do Portal (``app/security/uploads.py``), fonte única para não
    o WhatsApp aceitar algo que o Portal recusaria. ``midia_nome`` (só
    documento costuma ter — a Meta não manda ``filename`` de foto) decide a
    extensão; sem ele, tenta adivinhar pelo MIME real (:func:`extensao_provavel`)
    e, se for ambíguo (ex.: ``application/zip``, que serve pra docx/xlsx/pptx),
    cai num nome sem extensão reconhecida — ``validar_anexo`` recusa em vez de
    arriscar o tipo errado. Levanta ``UploadInvalido`` quando o tipo/conteúdo é
    rejeitado — o chamador decide se avisa quem mandou. ``None`` só quando o
    DOWNLOAD falha (rede, mídia expirada): não há o que dizer sobre isso."""
    from app.security.uploads import extensao_provavel, validar_anexo
    from app.whatsapp_client import baixar_midia

    midia_id = msg.get("midia_id")
    if not midia_id:
        return None
    baixado = await baixar_midia(str(midia_id))
    if baixado is None:
        return None
    conteudo, mime = baixado
    nome = msg.get("midia_nome") or f"whatsapp.{extensao_provavel(mime) or 'bin'}"
    return validar_anexo(nome, conteudo, max_bytes=settings.anexo_max_bytes)


async def _subir_anexo_chamado(
    validado: Any,
    perfil: dict[str, Any],
    claims: dict[str, str],
    chamado_id: str,
    repo: Any,
    settings: Settings,
) -> bool:
    """Sobe um anexo já validado para o Storage e registra como mensagem do
    chamado. Upload via ``service_role`` (o usuário não tem sessão HTTP
    aqui): seguro porque o ``path`` é construído 100% pelo servidor a partir
    de ``empresa_id``/``chamado_id`` já validados pela RLS na criação — nada
    do payload do usuário entra nele. ``False`` quando o Storage não está
    configurado; nunca lança (quem chama decide a política de falha)."""
    from app.storage import AnexosStorage, ensure_storage

    if not settings.supabase_service_role_key:
        return False
    storage = await ensure_storage()
    if storage is None:
        return False
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
    return True


async def _anexar_midia_da_conversa(
    conversa: list[dict[str, Any]],
    perfil: dict[str, Any],
    claims: dict[str, str],
    chamado_id: str,
    repo: Any,
    settings: Settings,
) -> None:
    """Sobe a foto/documento mais recente da conversa de intake como anexo da
    1ª mensagem do chamado recém-criado. Nunca lança: falha de download,
    validação (tipo fora da allow-list) ou storage nunca derruba o chamado
    já criado — o anexo aqui é enriquecimento opcional (a pessoa ainda pode
    mandar de novo depois, dentro da janela pós-criação de
    :func:`_anexar_midia_pos_criacao`, dessa vez com confirmação)."""
    item = next((m for m in reversed(conversa) if m.get("midia_id")), None)
    if item is None:
        return
    try:
        validado = await _midia_valida(item, settings)
        if validado is None:
            return
        await _subir_anexo_chamado(validado, perfil, claims, chamado_id, repo, settings)
    except Exception as exc:  # noqa: BLE001 — anexo é opcional (inclui UploadInvalido)
        log.warning("[WA INTAKE] Anexo do WhatsApp falhou (chamado %s): %s", chamado_id, exc)


_TEXTO_ANEXO_FALHOU = (
    "Não consegui salvar esse arquivo agora. Pode tentar mandar de novo daqui a pouco?"
)


def _texto_anexo_ok(codigo: str) -> str:
    emoji = random.choice(_EMOJIS_CONFIRMACAO)
    return f"Anexei ao chamado {codigo} {emoji}"


async def _anexar_midia_pos_criacao(
    alvo: dict[str, Any],
    perfil: dict[str, Any],
    msg: dict[str, Any],
    telefone: str,
    settings: Settings,
) -> None:
    """Anexa uma foto/documento recebido DENTRO da janela pós-criação
    (``WHATSAPP_INTAKE_ANEXO_JANELA_S``) direto no chamado ``alvo``, fora do
    fluxo de intake — não passa por :func:`processar_conversa`/
    ``ia_whatsapp_intake`` porque não há decisão de IA aqui, só upload.

    Ao contrário do anexo durante o intake (:func:`_anexar_midia_da_conversa`,
    silencioso — o chamado já existe de qualquer forma), aqui a pessoa está
    esperando uma confirmação de que o arquivo chegou: erro de tipo/tamanho
    vira resposta explicando o motivo (``UploadInvalido`` já traz mensagem
    pronta em PT-BR), não silêncio. Nunca lança — task de fundo."""
    from app.repositories.chamados import ChamadosRepo
    from app.security.uploads import UploadInvalido

    claims = claims_do_perfil(perfil["id"])
    repo = ChamadosRepo()
    try:
        validado = await _midia_valida(msg, settings)
    except UploadInvalido as exc:
        await _responder(telefone, str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — nunca derruba a task de fundo
        log.warning(
            "[WA INTAKE] Anexo pós-criação falhou (chamado %s): %s", alvo["chamado_id"], exc
        )
        await _responder(telefone, _TEXTO_ANEXO_FALHOU)
        return
    if validado is None:
        await _responder(telefone, _TEXTO_ANEXO_FALHOU)
        return
    try:
        ok = await _subir_anexo_chamado(
            validado, perfil, claims, alvo["chamado_id"], repo, settings
        )
    except Exception as exc:  # noqa: BLE001 — nunca derruba a task de fundo
        log.warning(
            "[WA INTAKE] Upload pós-criação falhou (chamado %s): %s", alvo["chamado_id"], exc
        )
        ok = False
    await _responder(telefone, _texto_anexo_ok(alvo["codigo"]) if ok else _TEXTO_ANEXO_FALHOU)


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

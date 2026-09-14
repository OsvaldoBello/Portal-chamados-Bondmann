"""Fila de notificações ativas no WhatsApp (anti-flood).

PROPOSTA (docs/wuzapi/): copiar para `app/services/notificacoes_whatsapp.py`,
aplicar a migration `0091_whatsapp_notificacoes_fila.sql` e chamar
`iniciar_fila_notificacoes()` no lifespan de `app/main.py`.

Quem produz: as rotas que hoje chamam `agendar_notificacao_email`
(`app/routes/workspace.py`, `app/routes/portal.py`) passam a chamar também
`enfileirar_notificacao_chamado()`. Quem consome: **um** worker, que só
manda uma mensagem por vez respeitando

- intervalo mínimo global entre envios + jitter aleatório
  (`WHATSAPP_NOTIF_INTERVALO_MIN_S` / `WHATSAPP_NOTIF_JITTER_S`);
- intervalo mínimo por destinatário (`WHATSAPP_NOTIF_INTERVALO_CONTATO_S`) —
  três operadores respondendo o mesmo chamado em dez segundos viram uma
  mensagem, não três;
- janela de silêncio (`WHATSAPP_NOTIF_HORA_INICIO`/`_FIM`, fuso de São
  Paulo): notificação de helpdesk às 3h da manhã é o tipo de coisa que faz
  funcionário clicar em "Bloquear/Denunciar", que é o fator número 1 de
  banimento de um número comum.

Falha de envio não descarta: reagenda com backoff exponencial até
`_MAX_TENTATIVAS`, depois marca FALHOU (o e-mail continua sendo o canal
formal — o WhatsApp aqui é conveniência, não é a única via).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import admin_connection

log = logging.getLogger("app.services.notificacoes_whatsapp")

_TZ = ZoneInfo("America/Sao_Paulo")
_MAX_TENTATIVAS = 5
# Espera do worker quando a fila está vazia. Não precisa ser curto: quem
# enfileira acorda o worker (`_acordar`) em vez de esperar o próximo ciclo.
_INTERVALO_OCIOSO_S = 15.0

_evento_novidade = asyncio.Event()
_tarefa_worker: asyncio.Task | None = None


# --------------------------------------------------------------------------
# Produção (chamado pelas rotas)
# --------------------------------------------------------------------------


def texto_notificacao(
    *, codigo: str, titulo: str, autor_resposta: str, link: str, tipo: str = "resposta"
) -> str:
    """Texto padrão da notificação ativa.

    O formato é deliberado, e é ele que segura a taxa de denúncia em zero:

    1. **Identificação do chamado logo na primeira linha** (`[Chamado BND-123]`)
       — a pessoa reconhece antes de ler o resto que isso é continuação de
       algo que ELA abriu, não abordagem fria;
    2. quem respondeu, com nome — mensagem de pessoa, não de robô anônimo;
    3. o que fazer a seguir, com link do portal;
    4. lembrete de que dá para responder ali mesmo — conversa de mão dupla é
       sinal de conta legítima para o WhatsApp, e monólogo é sinal de spam.

    Sem emoji de propaganda, sem "🔥 novidade", sem link encurtado (encurtador
    é marcador clássico de spam e pesa contra o número)."""
    rotulos = {
        "resposta": "recebeu uma resposta",
        "status": "mudou de status",
        "conclusao": "foi concluído",
    }
    return (
        f"[Chamado {codigo}] {titulo}\n\n"
        f"Seu chamado {rotulos.get(tipo, 'foi atualizado')} — {autor_resposta} acabou de responder.\n\n"
        f"Ver no portal: {link}\n\n"
        "Se quiser, pode responder por aqui mesmo que eu registro no chamado."
    )


async def enfileirar_notificacao_chamado(
    *,
    telefone: str,
    corpo: str,
    perfil_id: str | None = None,
    chamado_id: str | None = None,
    dedup_key: str | None = None,
) -> bool:
    """Coloca uma notificação na fila. Nunca lança.

    `dedup_key` (ex.: `f"chamado:{chamado_id}:resposta"`) faz a segunda
    chamada ser ignorada enquanto a primeira não saiu — é o que evita três
    mensagens quando três operadores respondem em sequência."""
    settings = get_settings()
    if not settings.whatsapp_notificacao_ativa or not telefone or not corpo:
        return False
    try:
        async with admin_connection() as conn:
            inserida = await conn.fetchval(
                """
                INSERT INTO whatsapp_notificacoes_fila
                       (telefone, corpo, perfil_id, chamado_id, dedup_key)
                VALUES ($1, $2, $3::uuid, $4::uuid, $5)
                ON CONFLICT DO NOTHING
                RETURNING id
                """,
                telefone,
                corpo,
                perfil_id,
                chamado_id,
                dedup_key,
            )
    except Exception as exc:  # noqa: BLE001 — notificação nunca derruba a rota
        log.warning("[WA NOTIF] Falha ao enfileirar: %s", type(exc).__name__)
        return False

    if inserida is not None:
        _evento_novidade.set()
    return inserida is not None


# --------------------------------------------------------------------------
# Consumo (worker único)
# --------------------------------------------------------------------------


def _dentro_da_janela(agora: datetime | None = None) -> bool:
    settings = get_settings()
    agora = agora or datetime.now(_TZ)
    inicio = time(hour=settings.whatsapp_notificacao_hora_inicio)
    fim = time(hour=settings.whatsapp_notificacao_hora_fim)
    return inicio <= agora.timetz().replace(tzinfo=None) < fim


def _proxima_abertura(agora: datetime | None = None) -> datetime:
    """Início da próxima janela permitida (hoje mais tarde ou amanhã cedo)."""
    settings = get_settings()
    agora = agora or datetime.now(_TZ)
    hoje = agora.replace(
        hour=settings.whatsapp_notificacao_hora_inicio, minute=0, second=0, microsecond=0
    )
    return hoje if agora < hoje else hoje + timedelta(days=1)


async def _proxima_da_fila(conn) -> dict | None:
    """Reserva uma notificação para este worker (FOR UPDATE SKIP LOCKED).

    SKIP LOCKED mesmo com um worker só: se um dia a app subir com duas
    réplicas no Railway, duas mensagens iguais para a mesma pessoa seria
    exatamente o comportamento que essa fila existe para impedir."""
    linha = await conn.fetchrow(
        """
        UPDATE whatsapp_notificacoes_fila f
           SET status = 'ENVIANDO',
               tentativas = f.tentativas + 1
         WHERE f.id = (
                 SELECT id
                   FROM whatsapp_notificacoes_fila
                  WHERE status = 'PENDENTE'
                    AND agendada_para <= now()
                  ORDER BY agendada_para, id
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
               )
     RETURNING f.id, f.telefone, f.corpo, f.tentativas
        """
    )
    return dict(linha) if linha else None


async def _adiar(conn, id_: int, segundos: float, *, erro: str | None = None) -> None:
    await conn.execute(
        """
        UPDATE whatsapp_notificacoes_fila
           SET status = 'PENDENTE',
               agendada_para = now() + make_interval(secs => $2),
               erro = COALESCE($3, erro)
         WHERE id = $1
        """,
        id_,
        float(segundos),
        erro,
    )


async def _espera_por_contato(conn, telefone: str) -> float:
    """Segundos que ainda faltam para poder mandar de novo a este número."""
    settings = get_settings()
    minimo = settings.whatsapp_notificacao_intervalo_contato_s
    if minimo <= 0:
        return 0.0
    passados = await conn.fetchval(
        """
        SELECT EXTRACT(EPOCH FROM (now() - MAX(enviada_em)))
          FROM whatsapp_notificacoes_fila
         WHERE telefone = $1 AND status = 'ENVIADA'
        """,
        telefone,
    )
    if passados is None:
        return 0.0
    return max(0.0, minimo - float(passados))


async def _ciclo() -> float:
    """Processa no máximo uma notificação. Devolve quanto esperar até a próxima.

    Uma por ciclo, de propósito: a cadência do número é a única coisa que
    separa "sistema corporativo" de "disparador em massa" aos olhos do
    WhatsApp."""
    settings = get_settings()

    if not _dentro_da_janela():
        return max(60.0, (_proxima_abertura() - datetime.now(_TZ)).total_seconds())

    async with admin_connection() as conn:
        item = await _proxima_da_fila(conn)
        if item is None:
            return _INTERVALO_OCIOSO_S

        faltam = await _espera_por_contato(conn, item["telefone"])
        if faltam > 0:
            await _adiar(conn, item["id"], faltam)
            return min(faltam, _INTERVALO_OCIOSO_S)

    from app.whatsapp_client import responder_humanizado

    try:
        await responder_humanizado(item["telefone"], item["corpo"])
    except Exception as exc:  # noqa: BLE001 — qualquer falha vira reagendamento
        motivo = type(exc).__name__
        async with admin_connection() as conn:
            if item["tentativas"] >= _MAX_TENTATIVAS:
                await conn.execute(
                    "UPDATE whatsapp_notificacoes_fila SET status='FALHOU', erro=$2 WHERE id=$1",
                    item["id"],
                    motivo,
                )
                log.warning("[WA NOTIF] Notificação %s abandonada após %s tentativas (%s)",
                            item["id"], item["tentativas"], motivo)
            else:
                # Backoff exponencial com teto de 30 min: se o wuzapi caiu ou
                # a sessão desconectou, martelar não ajuda e ainda enche o log.
                espera = min(1800.0, 30.0 * (2 ** (item["tentativas"] - 1)))
                await _adiar(conn, item["id"], espera, erro=motivo)
                log.info("[WA NOTIF] Reagendada em %.0fs (%s)", espera, motivo)
        return 5.0

    async with admin_connection() as conn:
        await conn.execute(
            "UPDATE whatsapp_notificacoes_fila SET status='ENVIADA', enviada_em=now(), erro=NULL WHERE id=$1",
            item["id"],
        )

    # Intervalo mínimo + jitter: envios espaçados de forma irregular, nunca
    # num relógio perfeito (cadência exata é assinatura de robô).
    return settings.whatsapp_notificacao_intervalo_min_s + random.uniform(
        0.0, settings.whatsapp_notificacao_jitter_s
    )


async def _loop() -> None:
    log.info("[WA NOTIF] Worker de notificações WhatsApp iniciado.")
    while True:
        try:
            espera = await _ciclo()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — o worker não pode morrer
            log.warning("[WA NOTIF] Ciclo falhou (%s) — seguindo.", type(exc).__name__)
            espera = _INTERVALO_OCIOSO_S

        _evento_novidade.clear()
        try:
            # Acorda antes da hora se alguém enfileirar algo novo.
            await asyncio.wait_for(_evento_novidade.wait(), timeout=espera)
        except TimeoutError:
            pass


def iniciar_fila_notificacoes() -> None:
    """Sobe o worker (idempotente) — chamar no lifespan de `app/main.py`."""
    global _tarefa_worker
    settings = get_settings()
    if not settings.whatsapp_notificacao_ativa:
        return
    if _tarefa_worker is not None and not _tarefa_worker.done():
        return
    _tarefa_worker = asyncio.create_task(_loop())


async def parar_fila_notificacoes() -> None:
    """Encerra o worker no shutdown."""
    global _tarefa_worker
    if _tarefa_worker is None:
        return
    _tarefa_worker.cancel()
    try:
        await _tarefa_worker
    except asyncio.CancelledError:
        pass
    _tarefa_worker = None

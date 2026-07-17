"""MFA (TOTP) via GoTrue — enrollment, verificação (step-up) e reset por admin.

Fatia Fase 1 do faseamento da Seção 3.4.1 do plano mestre (item 3.3 do plano de
melhorias). Princípios:

- **O segredo TOTP vive só no GoTrue** — nunca no nosso banco (mesma delegação de
  auth já adotada, Seção 3.4.1). O app apenas orquestra os fluxos GoTrue
  (`enroll → challenge → verify`) sobre a sessão do usuário.
- **Espelho booleano `app_metadata.mfa_enabled` no JWT** (mesmo padrão de
  dual-write do papel, item 1.5): permite o enforcement em ``app/auth/dependencies``
  ler o estado de MFA **localmente** (claim já no token), sem uma chamada de rede
  ao GoTrue por request. É só um booleano — não é segredo.
- **Recovery = reset por admin/TI** (decisão do gestor, 2026-07-16): remover o(s)
  fator(es) via Admin API (service_role); o usuário re-enrola. Sem recovery codes
  guardados por nós.

Cada operação abre um **cliente isolado** (sessão própria) — reusar o cliente
global compartilhado causaria corrida entre requests, mesma razão do fluxo de
redefinição de senha (`create_isolated_client`, Seção 3.4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

from app.auth.session import SessionTokens
from app.auth.supabase_client import create_isolated_client, ensure_admin_client

log = logging.getLogger("app.auth.mfa")

# Nome amigável do fator (aparece no app autenticador). Único fator por usuário
# nesta fatia — não expomos múltiplos fatores.
FRIENDLY_NAME = "Portal Bondmann"

# Prefixo que o gotrue-py concatena ao SVG do QR (ver `_enroll` na lib).
_PREFIXO_QR_GOTRUE = "data:image/svg+xml;utf-8,"


def _qr_data_uri(qr_code: str) -> str:
    """Normaliza o QR do GoTrue num data URI seguro para ``<img src>``.

    O gotrue-py monta ``data:image/svg+xml;utf-8,<svg ...>`` concatenando o SVG
    **cru**, sem percent-encoding: um ``#`` (cor no SVG) truncaria a URI no
    fragmento e a imagem não renderizaria. Reencodamos o payload por garantia.
    Data URI é compatível com a CSP estrita do projeto (``img-src 'self' data:``,
    Seção 3.8) — nada de host externo."""
    svg = qr_code[len(_PREFIXO_QR_GOTRUE):] if qr_code.startswith(_PREFIXO_QR_GOTRUE) else qr_code
    return "data:image/svg+xml;charset=utf-8," + quote(svg, safe="")


class MfaErro(Exception):
    """Falha numa operação de MFA (enroll/challenge/verify/list)."""


@dataclass(frozen=True)
class EnrollTotp:
    """Dados do enrollment a exibir UMA vez ao usuário."""

    factor_id: str
    qr_code: str   # já vem como `data:image/svg+xml;utf-8,...` (CSP `img-src data:`)
    secret: str    # entrada manual quando não dá para escanear o QR
    uri: str       # otpauth://... (deep-link do autenticador)


async def _cliente_com_sessao(tokens: SessionTokens):
    """Cliente isolado com a sessão do usuário aplicada (para as chamadas MFA que
    operam sobre `get_session()` do GoTrue)."""
    client = await create_isolated_client()
    await client.auth.set_session(tokens.access_token, tokens.refresh_token)
    return client


async def iniciar_enroll(tokens: SessionTokens) -> EnrollTotp:
    """Cria um fator TOTP **não verificado** e devolve QR/segredo/uri.

    O fator só é ativado depois que :func:`confirmar` verifica um código do
    autenticador (challenge → verify)."""
    client = await _cliente_com_sessao(tokens)
    try:
        resp = await client.auth.mfa.enroll(
            {"factor_type": "totp", "friendly_name": FRIENDLY_NAME}
        )
    except Exception as exc:  # noqa: BLE001 — GoTrue indisponível/limite de fatores
        raise MfaErro(f"enroll falhou: {type(exc).__name__}") from exc
    totp = getattr(resp, "totp", None)
    if totp is None:
        raise MfaErro("enroll não retornou dados TOTP")
    return EnrollTotp(
        factor_id=resp.id,
        qr_code=_qr_data_uri(totp.qr_code),
        secret=totp.secret,
        uri=totp.uri,
    )


async def confirmar(tokens: SessionTokens, factor_id: str, codigo: str) -> SessionTokens:
    """challenge + verify de ``factor_id`` com ``codigo``.

    A primeira verificação bem-sucedida **ativa** o fator e eleva a sessão a
    ``aal2`` (o GoTrue desloga as outras sessões do usuário). Retorna os novos
    tokens (aal2) para regravar nos cookies. Levanta :class:`MfaErro` em código
    inválido/expirado."""
    client = await _cliente_com_sessao(tokens)
    try:
        desafio = await client.auth.mfa.challenge({"factor_id": factor_id})
        verificado = await client.auth.mfa.verify(
            {"factor_id": factor_id, "challenge_id": desafio.id, "code": codigo}
        )
    except Exception as exc:  # noqa: BLE001 — código errado/expirado, fator inexistente
        raise MfaErro(f"verify falhou: {type(exc).__name__}") from exc
    return SessionTokens(verificado.access_token, verificado.refresh_token)


async def fator_verificado_id(tokens: SessionTokens) -> str | None:
    """Id do fator TOTP **verificado** do usuário (para o step-up no /mfa/verify).

    ``None`` se o usuário não tem fator verificado. Usa a sessão do próprio
    usuário (não a Admin API)."""
    client = await _cliente_com_sessao(tokens)
    try:
        # get_user() atualiza os fatores na sessão antes do list_factors (cacheado).
        await client.auth.get_user()
        fatores = await client.auth.mfa.list_factors()
    except Exception as exc:  # noqa: BLE001
        raise MfaErro(f"list_factors falhou: {type(exc).__name__}") from exc
    for f in fatores.totp:
        if f.status == "verified":
            return f.id
    return None


async def marcar_mfa_habilitado(user_id: str, habilitado: bool) -> None:
    """Espelha ``app_metadata.mfa_enabled`` no JWT via Admin API (service_role).

    Mesmo dual-write do papel (item 1.5): o GoTrue faz **merge** de app_metadata,
    então gravar só ``mfa_enabled`` preserva ``role``. Sem service_role: loga e
    segue — o fator no GoTrue continua válido; só o enforcement por claim degrada
    para o comportamento de transição (nudge)."""
    client = await ensure_admin_client()
    if client is None:
        log.warning(
            "MFA: service_role ausente — app_metadata.mfa_enabled não espelhado (user=%s)",
            user_id,
        )
        return
    try:
        await client.auth.admin.update_user_by_id(
            user_id, {"app_metadata": {"mfa_enabled": habilitado}}
        )
    except Exception as exc:  # noqa: BLE001
        log.error(
            "MFA: falha ao espelhar app_metadata.mfa_enabled (user=%s habilitado=%s): %s",
            user_id, habilitado, type(exc).__name__,
        )


async def resetar_mfa(user_id: str) -> int:
    """Remove TODOS os fatores MFA do usuário via Admin API e limpa a flag.

    Recovery por admin/TI (decisão do gestor). Deslogar o usuário de todas as
    sessões é efeito do próprio GoTrue ao remover um fator verificado. Retorna
    quantos fatores foram removidos. Levanta :class:`MfaErro` sem service_role."""
    client = await ensure_admin_client()
    if client is None:
        raise MfaErro("service_role não configurada")
    try:
        listagem = await client.auth.admin.mfa.list_factors({"user_id": user_id})
    except Exception as exc:  # noqa: BLE001
        raise MfaErro(f"list_factors (admin) falhou: {type(exc).__name__}") from exc
    fatores = getattr(listagem, "factors", None) or []
    removidos = 0
    for f in fatores:
        try:
            await client.auth.admin.mfa.delete_factor({"user_id": user_id, "id": f.id})
            removidos += 1
        except Exception as exc:  # noqa: BLE001 — segue removendo os demais
            log.error("MFA reset: falha ao remover fator %s (user=%s): %s",
                      f.id, user_id, type(exc).__name__)
    await marcar_mfa_habilitado(user_id, False)
    return removidos

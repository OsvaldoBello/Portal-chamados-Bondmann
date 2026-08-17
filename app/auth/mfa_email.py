"""MFA por código de e-mail (alternativa ao TOTP) — mecanismo próprio do app.

O GoTrue só aceita fatores ``totp``/``phone``/``webauthn`` (ver
``supabase/auth``, ``internal/api/mfa.go``) — não existe fator "email". Por
isso este código nunca chega perto do GoTrue: é gerado, guardado (como hash)
e conferido inteiramente aqui, em ``mfa_email_codes`` (migration 0083). A
elevação de sessão correspondente é local também — ver
``app/auth/mfa_email_stepup.py`` — e **não** mexe na claim ``aal`` do JWT
(essa continua sendo escrita só pelo GoTrue, para o fluxo TOTP).

Um único código pendente por usuário: cada novo envio apaga o anterior.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.db import admin_connection

CODIGO_TTL_SEGUNDOS = 600          # 10 minutos
MAX_TENTATIVAS = 5
REENVIO_COOLDOWN_SEGUNDOS = 60


class MfaErro(Exception):
    """Falha ao gerar/enviar ou verificar o código por e-mail."""


def _hash_codigo(codigo: str) -> str:
    """HMAC-SHA256 com o segredo de sessão do app — nunca guardamos o código
    em texto claro (mesmo rigor de senha, ainda que seja um código curto e
    efêmero)."""
    chave = get_settings().session_secret.encode("utf-8")
    return hmac.new(chave, codigo.encode("utf-8"), hashlib.sha256).hexdigest()


def _gerar_codigo() -> str:
    """6 dígitos via CSPRNG (``secrets``, não ``random``)."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def enviar_codigo(user_id: str, email: str) -> None:
    """Gera um novo código, invalida qualquer pendente e envia por e-mail.

    Levanta :class:`MfaErro` se o último envio para este usuário foi há menos
    de :data:`REENVIO_COOLDOWN_SEGUNDOS` (barreira contra spam na caixa de
    entrada do próprio usuário) ou se o envio do e-mail falhar."""
    agora = datetime.now(UTC)
    async with admin_connection() as conn:
        ultimo = await conn.fetchval(
            "SELECT created_at FROM mfa_email_codes WHERE user_id = $1::uuid "
            "ORDER BY created_at DESC LIMIT 1",
            user_id,
        )
        if ultimo is not None and (agora - ultimo).total_seconds() < REENVIO_COOLDOWN_SEGUNDOS:
            raise MfaErro("aguarde antes de pedir um novo código")

        codigo = _gerar_codigo()
        await conn.execute("DELETE FROM mfa_email_codes WHERE user_id = $1::uuid", user_id)
        await conn.execute(
            "INSERT INTO mfa_email_codes (user_id, codigo_hash, expira_em) "
            "VALUES ($1::uuid, $2, $3)",
            user_id,
            _hash_codigo(codigo),
            agora + timedelta(seconds=CODIGO_TTL_SEGUNDOS),
        )

    from app.notification import enviar_codigo_mfa_email

    enviado = await enviar_codigo_mfa_email(email, codigo)
    if not enviado:
        raise MfaErro("falha ao enviar o e-mail com o código")


async def verificar_codigo(user_id: str, codigo: str) -> bool:
    """Confere ``codigo`` contra o pendente do usuário. Uso único: o registro
    é apagado tanto no sucesso quanto no esgotamento (expirado/tentativas).

    Levanta :class:`MfaErro` em qualquer caso de recusa — o chamador decide a
    mensagem exibida."""
    agora = datetime.now(UTC)
    async with admin_connection() as conn:
        linha = await conn.fetchrow(
            "SELECT id, codigo_hash, tentativas, expira_em FROM mfa_email_codes "
            "WHERE user_id = $1::uuid",
            user_id,
        )
        if linha is None:
            raise MfaErro("nenhum código pendente — peça um novo")
        if linha["expira_em"] < agora:
            await conn.execute("DELETE FROM mfa_email_codes WHERE id = $1", linha["id"])
            raise MfaErro("código expirado")
        if linha["tentativas"] >= MAX_TENTATIVAS:
            await conn.execute("DELETE FROM mfa_email_codes WHERE id = $1", linha["id"])
            raise MfaErro("muitas tentativas — peça um novo código")

        if not hmac.compare_digest(linha["codigo_hash"], _hash_codigo(codigo)):
            await conn.execute(
                "UPDATE mfa_email_codes SET tentativas = tentativas + 1 WHERE id = $1",
                linha["id"],
            )
            raise MfaErro("código inválido")

        await conn.execute("DELETE FROM mfa_email_codes WHERE id = $1", linha["id"])
    return True


async def limpar_pendentes(user_id: str) -> None:
    """Apaga qualquer código pendente do usuário (usado pelo reset de MFA por
    TI, ``app/auth/mfa.py::resetar_mfa``)."""
    async with admin_connection() as conn:
        await conn.execute("DELETE FROM mfa_email_codes WHERE user_id = $1::uuid", user_id)

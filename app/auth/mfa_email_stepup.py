"""Sinal de "esta sessão de login foi verificada por código de e-mail".

Cookie assinado (``itsdangerous``, mesmo padrão de ``app/auth/mfa_remember.py``
e ``app/security/csrf.py``) contendo o ``user_id``. Existe porque o GoTrue não
tem fator MFA de e-mail (ver ``app/auth/mfa_email.py``) — não há como um
código conferido só no nosso banco elevar a claim ``aal`` do JWT, que é
escrita inteiramente pelo GoTrue. Este cookie é o substituto local, no MESMO
espírito do cookie de "lembrar dispositivo": não eleva a sessão a ``aal2`` de
verdade, só é tratado como equivalente pelo nosso próprio enforcement
(``app/auth/dependencies.py::sessao_mfa_satisfeita``).

Diferença deliberada em relação a "lembrar dispositivo": aquele sobrevive ao
logout de propósito (confiança de 30 dias no NAVEGADOR). Este é escopo de
UMA sessão de login (confirmação de que ESTA sessão passou pelo segundo
fator) — por isso ``app/auth/routes.py::logout()`` o limpa."""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings

EMAIL_STEPUP_COOKIE = "mfa_email_verificado"
_SALT = "mfa-email-stepup"
MAX_AGE_SEGUNDOS = 60 * 60 * 24 * 30  # mesmo teto do refresh token; logout limpa antes disso


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=_SALT)


def marcar_email_verificado(response: Response, settings: Settings, user_id: str) -> None:
    """Grava o cookie de "sessão verificada por e-mail" para ``user_id``."""
    token = _serializer(settings).dumps(user_id)
    response.set_cookie(
        EMAIL_STEPUP_COOKIE,
        token,
        max_age=MAX_AGE_SEGUNDOS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def email_verificado(request: Request, settings: Settings, user_id: str) -> bool:
    """``True`` se esta sessão já verificou o código de e-mail (e o cookie
    pertence ao ``user_id`` corrente — nunca de outro usuário)."""
    token = request.cookies.get(EMAIL_STEPUP_COOKIE)
    if not token:
        return False
    try:
        valor = _serializer(settings).loads(token, max_age=MAX_AGE_SEGUNDOS)
    except (BadSignature, SignatureExpired):
        return False
    return valor == user_id

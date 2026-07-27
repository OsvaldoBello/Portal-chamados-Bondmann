""""Lembrar este dispositivo" do MFA — pedido do usuário (2026-07-27).

Cookie assinado (``itsdangerous``, mesmo padrão de ``app/security/csrf.py``)
contendo o ``user_id``, com expiração própria de **30 dias**. Marcado no
checkbox de `/mfa/verify` ou `/mfa/enroll/confirmar`.

Importante: isto **não** eleva a sessão a ``aal2`` no GoTrue — só isso exige o
código do autenticador (``app/auth/mfa.py::confirmar``). É uma checagem extra,
só do nosso app, no gate de ``enforce_admin_mfa``: se o cookie for válido para
o usuário da sessão corrente, pulamos o redirect para o step-up. Por ser
puramente local (sem chamada ao GoTrue), mantém a mesma característica de
"enforcement sem ida à rede" do resto do MFA (Seção 3.4.1 do plano mestre).

Limitação aceita: como não há registro server-side de dispositivos, um reset
de MFA pelo TI (recovery) não revoga um cookie de "dispositivo confiável" já
emitido noutro navegador do mesmo usuário — só expira sozinho em 30 dias ou é
apagado no logout/reset local de cookies. Fora de escopo aqui (exigiria uma
lista de dispositivos por usuário, não pedida)."""

from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings

REMEMBER_COOKIE = "mfa_lembrar_dispositivo"
_SALT = "mfa-remember-device"
MAX_AGE_SEGUNDOS = 60 * 60 * 24 * 30  # 30 dias


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=_SALT)


def lembrar_dispositivo(response: Response, settings: Settings, user_id: str) -> None:
    """Grava o cookie de dispositivo confiável para ``user_id`` (30 dias)."""
    token = _serializer(settings).dumps(user_id)
    response.set_cookie(
        REMEMBER_COOKIE,
        token,
        max_age=MAX_AGE_SEGUNDOS,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def dispositivo_confiavel(request: Request, settings: Settings, user_id: str) -> bool:
    """``True`` se este navegador já tem o cookie válido (não expirado) e ele
    pertence ao ``user_id`` da sessão corrente (nunca de outro usuário — ex.:
    troca de conta no mesmo navegador)."""
    token = request.cookies.get(REMEMBER_COOKIE)
    if not token:
        return False
    try:
        valor = _serializer(settings).loads(token, max_age=MAX_AGE_SEGUNDOS)
    except (BadSignature, SignatureExpired):
        return False
    return valor == user_id

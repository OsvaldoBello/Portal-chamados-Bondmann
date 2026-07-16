"""Proteção CSRF — double-submit cookie + header (Seção 3.5).

Token assinado com ``itsdangerous``, entregue em cookie legível e ecoado pelo
cliente no header ``X-CSRF-Token`` (injeção global via ``hx-headers`` do HTMX)
ou em campo hidden nos forms. Toda mutação (POST/PUT/PATCH/DELETE) valida que
o header bate com o cookie assinado.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request, status
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import Settings

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_FORM_FIELD = "csrf_token"
_SALT = "csrf-double-submit"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}


class CSRFProtect:
    def __init__(self, settings: Settings) -> None:
        self._serializer = URLSafeSerializer(settings.csrf_secret, salt=_SALT)
        self._secure = settings.cookie_secure

    def issue(self) -> str:
        """Gera um token CSRF assinado (valor aleatório + assinatura)."""
        return self._serializer.dumps(secrets.token_urlsafe(32))

    def get_or_issue(self, request: Request) -> str:
        """Reusa o cookie CSRF **válido** se já existir; só emite um novo quando
        ausente/adulterado. Evita rotacionar o token a cada render — a rotação
        fazia o token do formulário visível divergir do cookie (ex.: página
        restaurada do cache), causando 'CSRF inválido ou ausente' ao submeter."""
        existing = request.cookies.get(CSRF_COOKIE)
        if existing is not None and self._unsign(existing):
            return existing
        return self.issue()

    def _unsign(self, token: str | None) -> str | None:
        if not token:
            return None
        try:
            return self._serializer.loads(token)
        except BadSignature:
            return None

    def set_cookie(self, response, token: str) -> None:
        # httpOnly=False: o cliente precisa ler o cookie para ecoar no header.
        response.set_cookie(
            CSRF_COOKIE,
            token,
            httponly=False,
            secure=self._secure,
            samesite="lax",
            path="/",
        )

    async def validate(self, request: Request) -> None:
        """Valida a requisição; levanta 403 se o token CSRF for inválido."""
        if request.method in _SAFE_METHODS:
            return
        cookie_token = request.cookies.get(CSRF_COOKIE)
        sent = request.headers.get(CSRF_HEADER)
        if sent is None:
            # Permite token via campo de formulário (forms não-HTMX).
            try:
                form = await request.form()
                sent = form.get(CSRF_FORM_FIELD)  # type: ignore[assignment]
            except Exception:
                sent = None

        cookie_val = self._unsign(cookie_token)
        sent_val = self._unsign(sent)
        if not cookie_val or not sent_val or not secrets.compare_digest(cookie_val, sent_val):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF token inválido ou ausente.",
            )


_csrf: CSRFProtect | None = None


def init_csrf(settings: Settings) -> CSRFProtect:
    global _csrf
    if _csrf is None:
        _csrf = CSRFProtect(settings)
    return _csrf


def get_csrf() -> CSRFProtect:
    if _csrf is None:
        raise RuntimeError("CSRFProtect não inicializado.")
    return _csrf

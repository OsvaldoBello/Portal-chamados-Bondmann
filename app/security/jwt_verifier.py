"""Verificação local do JWT do Supabase (Seção 3.6).

Decisão: **signing keys assimétricas via JWKS** (RS256/ES256) quando o projeto
suportar — verificação por chave pública, com JWKS cacheado e rotação. Fallback
para **HS256** com o JWT secret legado se for o modo provisionado.

Valida assinatura, ``exp``, ``aud`` (``authenticated``) e ``iss``, com uma folga
de relógio (``leeway``) para ``exp``/``iat``/``nbf`` — ver ``_LEEWAY_SEGUNDOS``.
"""

from __future__ import annotations

import jwt
from jwt import PyJWKClient

from app.config import Settings

EXPECTED_AUD = "authenticated"
# Tolerância de relógio (exp/iat/nbf) entre quem emite (Supabase) e quem verifica
# (este processo) — sem isso, um desvio de poucos segundos no relógio local (comum
# sem NTP contínuo, ex.: Windows sincroniza só periodicamente) rejeita todo login
# com "token not yet valid (iat)" mesmo com credenciais corretas (bug real, dev
# local, 2026-07-21). PyJWT aplica a mesma folga aos três claims de tempo.
_LEEWAY_SEGUNDOS = 10


class TokenInvalido(Exception):
    """Token ausente, expirado, com assinatura inválida ou claims incorretos."""


class JWTVerifier:
    """Verificador com cache de JWKS (assimétrico) e fallback HS256."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._issuer = f"{settings.supabase_url.rstrip('/')}/auth/v1"
        # PyJWKClient cacheia as chaves e lida com rotação por `kid`.
        self._jwks_client = PyJWKClient(settings.jwks_url) if settings.supabase_url else None

    def verify(self, token: str) -> dict:
        """Retorna os claims se válido; levanta ``TokenInvalido`` caso contrário."""
        if not token:
            raise TokenInvalido("token vazio")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenInvalido(f"header inválido: {exc}") from exc

        alg = header.get("alg", "")
        try:
            if alg.startswith(("RS", "ES")):
                return self._verify_asymmetric(token, alg)
            if alg == "HS256":
                return self._verify_hs256(token)
            raise TokenInvalido(f"algoritmo não suportado: {alg}")
        except jwt.ExpiredSignatureError as exc:
            raise TokenInvalido("token expirado") from exc
        except jwt.PyJWTError as exc:
            raise TokenInvalido(f"verificação falhou: {exc}") from exc

    def _verify_asymmetric(self, token: str, alg: str) -> dict:
        if self._jwks_client is None:
            raise TokenInvalido("JWKS não configurado")
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[alg],
            audience=EXPECTED_AUD,
            issuer=self._issuer,
            leeway=_LEEWAY_SEGUNDOS,
            options={"require": ["exp", "sub"]},
        )

    def _verify_hs256(self, token: str) -> dict:
        secret = self._settings.supabase_jwt_secret
        if not secret:
            raise TokenInvalido("HS256 sem JWT secret configurado")
        return jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=EXPECTED_AUD,
            issuer=self._issuer,
            leeway=_LEEWAY_SEGUNDOS,
            options={"require": ["exp", "sub"]},
        )


_verifier: JWTVerifier | None = None


def init_verifier(settings: Settings) -> JWTVerifier:
    global _verifier
    if _verifier is None:
        _verifier = JWTVerifier(settings)
    return _verifier


def get_verifier() -> JWTVerifier:
    if _verifier is None:
        raise RuntimeError("JWTVerifier não inicializado.")
    return _verifier

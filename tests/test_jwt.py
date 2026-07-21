"""Verificação de JWT — caminho HS256 (Seção 3.6)."""

import time

import jwt
import pytest

from app.config import Settings
from app.security.jwt_verifier import EXPECTED_AUD, JWTVerifier, TokenInvalido

_URL = "https://projeto.supabase.co"
_SECRET = "segredo-de-teste-hs256"


def _settings() -> Settings:
    return Settings(supabase_url=_URL, supabase_jwt_secret=_SECRET)


def _make_token(secret: str = _SECRET, **overrides) -> str:
    claims = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": EXPECTED_AUD,
        "iss": f"{_URL}/auth/v1",
        "email": "user@exemplo.com",
        "exp": int(time.time()) + 3600,
        "app_metadata": {"role": "CLIENTE"},
    }
    claims.update(overrides)
    return jwt.encode(claims, secret, algorithm="HS256")


def test_hs256_token_valido():
    verifier = JWTVerifier(_settings())
    claims = verifier.verify(_make_token())
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"
    assert claims["app_metadata"]["role"] == "CLIENTE"


def test_token_expirado():
    verifier = JWTVerifier(_settings())
    expired = _make_token(exp=int(time.time()) - 10)
    with pytest.raises(TokenInvalido):
        verifier.verify(expired)


def test_assinatura_invalida():
    verifier = JWTVerifier(_settings())
    forjado = _make_token(secret="outro-segredo")
    with pytest.raises(TokenInvalido):
        verifier.verify(forjado)


def test_audience_incorreta():
    verifier = JWTVerifier(_settings())
    with pytest.raises(TokenInvalido):
        verifier.verify(_make_token(aud="errada"))


def test_token_vazio():
    verifier = JWTVerifier(_settings())
    with pytest.raises(TokenInvalido):
        verifier.verify("")


def test_tolera_pequeno_desvio_de_relogio_no_iat():
    """Bug real (dev local, 2026-07-21): relógio do Windows uns segundos atrasado
    em relação ao Supabase (sem NTP contínuo) fazia todo login falhar com "token
    not yet valid (iat)", mesmo com credenciais corretas — o PyJWT rejeitava
    qualquer `iat` no "futuro" sem nenhuma tolerância. `leeway` cobre esse desvio."""
    verifier = JWTVerifier(_settings())
    token = _make_token(iat=int(time.time()) + 5)
    claims = verifier.verify(token)
    assert claims["sub"] == "11111111-1111-1111-1111-111111111111"


def test_ainda_rejeita_desvio_maior_que_a_folga():
    verifier = JWTVerifier(_settings())
    token = _make_token(iat=int(time.time()) + 3600)
    with pytest.raises(TokenInvalido):
        verifier.verify(token)

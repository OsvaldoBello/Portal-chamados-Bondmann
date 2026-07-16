"""Sprint 2 / item 2.9 (B5): claims adversariais em `_apply_rls_claims`.

`_apply_rls_claims` (app/db.py) embute os claims como literal SQL manualmente
escapado (dobra aspas simples) porque `SET LOCAL ROLE` + `set_config` vão numa
única simple query (sem bind de parâmetro do asyncpg para múltiplos comandos).
Estes testes não abrem conexão real — capturam a string SQL final via um fake
`Connection` e a decodificam de volta pelas regras de literal do Postgres
(única forma de escape sob `standard_conforming_strings`: `''` -> `'`),
confirmando que o round-trip preserva o claims original mesmo com aspas,
unicode, backslash e tentativas de fechar o literal cedo.
"""

import json

import pytest

from app.db import _apply_rls_claims

PREFIX = "SET LOCAL ROLE authenticated; SELECT set_config('request.jwt.claims', '"
SUFFIX = "', true);"


class _FakeConnection:
    def __init__(self) -> None:
        self.sql: str | None = None

    async def execute(self, sql: str) -> None:
        self.sql = sql


def _decode_claims_sql(sql: str) -> dict:
    assert sql.startswith(PREFIX), sql
    assert sql.endswith(SUFFIX), sql
    escaped_payload = sql[len(PREFIX) : -len(SUFFIX)]
    # Nenhuma aspas simples "solta" pode sobrar fora de um par duplicado —
    # senão o literal SQL teria sido fechado antes da hora (injeção).
    assert "'" not in escaped_payload.replace("''", "")
    return json.loads(escaped_payload.replace("''", "'"))


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "user-simples"},
        {"sub": "aspa simples: O'Brien"},
        {"sub": "aspas duplicadas: a''b"},
        {"sub": "tentativa de fechar cedo: '; DROP TABLE perfis; --"},
        {"sub": "aspas duplas: say \"hi\""},
        {"sub": "backslash: C:\\Windows\\path e \\'"},
        {"sub": "unicode: emoji 🎉 中文 عربى"},
        {"sub": "separadores de linha:   \n\t"},
        {
            "sub": "aninhado",
            "role": "authenticated",
            "nested": {"nome": "quote's here", "lista": ["x'y", "z\"w", "'''"]},
        },
    ],
)
@pytest.mark.asyncio
async def test_apply_rls_claims_round_trip_adversarial(claims: dict) -> None:
    conn = _FakeConnection()
    await _apply_rls_claims(conn, claims)
    assert conn.sql is not None
    assert _decode_claims_sql(conn.sql) == claims


@pytest.mark.asyncio
async def test_apply_rls_claims_seta_role_authenticated() -> None:
    conn = _FakeConnection()
    await _apply_rls_claims(conn, {"sub": "x"})
    assert conn.sql.startswith("SET LOCAL ROLE authenticated;")

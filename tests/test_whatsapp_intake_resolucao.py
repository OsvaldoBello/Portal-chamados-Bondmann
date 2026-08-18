"""Resolução telefone → perfil do intake WhatsApp.

Regra dura de produto: só número já cadastrado abre chamado, e ambiguidade
(mesmo telefone em 2+ perfis) NUNCA escolhe um — abriria chamado em nome da
pessoa errada.
"""

from contextlib import asynccontextmanager
from unittest.mock import patch

from app.ia import whatsapp_intake

_PERFIL = {
    "id": "perfil-uuid",
    "nome": "Fulano",
    "empresa_id": "empresa-uuid",
    "departamento_id": "dep-uuid",
    "departamento_nome": "Compras",
}


class _FakeConn:
    def __init__(self, linhas: list[dict]):
        self._linhas = linhas
        self.args: tuple | None = None

    async def fetch(self, sql: str, *args):
        assert "normalizar_telefone_br" in sql, "deve usar a função SQL da migration 0085"
        self.args = args
        return list(self._linhas)


@asynccontextmanager
async def _fake_admin_factory(conn):
    yield conn


def _patched(conn):
    def _fake_admin():
        return _fake_admin_factory(conn)

    return patch.object(whatsapp_intake, "admin_connection", _fake_admin)


async def test_um_match_devolve_perfil():
    conn = _FakeConn([_PERFIL])
    with _patched(conn):
        perfil = await whatsapp_intake.resolver_perfil_por_telefone("5551999998888")
    assert perfil is not None
    assert perfil["id"] == "perfil-uuid"
    assert conn.args == ("5551999998888",)


async def test_sem_match_devolve_none():
    with _patched(_FakeConn([])):
        assert await whatsapp_intake.resolver_perfil_por_telefone("5551999998888") is None


async def test_telefone_duplicado_nao_escolhe_ninguem():
    """Sem UNIQUE na coluna, 2+ perfis com o mesmo número são ambiguidade real."""
    outro = dict(_PERFIL, id="outro-uuid", nome="Sicrano")
    with _patched(_FakeConn([_PERFIL, outro])):
        assert await whatsapp_intake.resolver_perfil_por_telefone("5551999998888") is None

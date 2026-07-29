"""Testes do cliente REST do Storage de anexos (`app/storage.py`, Seção 3.9).

Sem rede: o `httpx.AsyncClient` é substituído por um fake que grava a chamada e
devolve a resposta combinada. O que importa provar aqui é o CONTRATO com a REST
do Supabase — URL montada, JWT do usuário no header (nunca a service_role),
`x-upsert: false` no bucket privado, TTL na assinatura — e a degradação graciosa
da signed URL (o template mostra "anexo indisponível" em vez de quebrar).
"""

from __future__ import annotations

import httpx
import pytest

from app import storage as storage_mod
from app.storage import AnexosStorage, StorageError

TOKEN = "jwt-do-usuario"
PATH = "empresa-1/chamado-9/arquivo.pdf"


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """Só implementa `.post` — é tudo que `AnexosStorage` usa."""

    def __init__(self, resposta: _Resp | Exception):
        self._resposta = resposta
        self.chamadas: list[dict] = []

    async def post(self, url, *, content=None, json=None, headers=None):
        self.chamadas.append({"url": url, "content": content, "json": json, "headers": headers})
        if isinstance(self._resposta, Exception):
            raise self._resposta
        return self._resposta


def _storage(resposta: _Resp | Exception) -> tuple[AnexosStorage, _FakeHttp]:
    http = _FakeHttp(resposta)
    return (
        AnexosStorage(
            base_url="https://proj.supabase.co/",  # barra final some (rstrip)
            bucket="chamados-anexos",
            ttl=3600,
            client=http,  # type: ignore[arg-type]
        ),
        http,
    )


@pytest.fixture(autouse=True)
def _reseta_singleton():
    """`_storage` é global de módulo — sem isso um teste vaza no outro."""
    storage_mod._storage = None
    yield
    storage_mod._storage = None


def test_path_e_tenant_scoped():
    # A convenção de path É a policy: `{empresa_id}/{chamado_id}/{arquivo}`.
    assert AnexosStorage.path("e1", "c9", "nota.pdf") == "e1/c9/nota.pdf"


async def test_upload_manda_jwt_do_usuario_e_nao_sobrescreve():
    st, http = _storage(_Resp(200))
    await st.upload(TOKEN, PATH, b"conteudo", "application/pdf")
    chamada = http.chamadas[0]
    assert chamada["url"] == f"https://proj.supabase.co/storage/v1/object/chamados-anexos/{PATH}"
    assert chamada["content"] == b"conteudo"
    assert chamada["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert chamada["headers"]["Content-Type"] == "application/pdf"
    assert chamada["headers"]["x-upsert"] == "false"


@pytest.mark.parametrize("status", [400, 403, 500])
async def test_upload_com_erro_vira_storage_error(status):
    st, _ = _storage(_Resp(status))
    with pytest.raises(StorageError):
        await st.upload(TOKEN, PATH, b"x", "application/pdf")


@pytest.mark.parametrize("chave", ["signedURL", "signedUrl"])
async def test_signed_url_aceita_as_duas_grafias_da_api(chave):
    st, http = _storage(_Resp(200, {chave: "/object/sign/anexos/arq?token=abc"}))
    url = await st.signed_url(TOKEN, PATH)
    assert url == "https://proj.supabase.co/storage/v1/object/sign/anexos/arq?token=abc"
    assert http.chamadas[0]["json"] == {"expiresIn": 3600}  # TTL de 1h (C2)


async def test_signed_url_com_erro_http_degrada_para_none():
    st, _ = _storage(_Resp(404))
    assert await st.signed_url(TOKEN, PATH) is None


async def test_signed_url_com_excecao_de_rede_degrada_para_none():
    st, _ = _storage(httpx.ConnectError("sem rede"))
    assert await st.signed_url(TOKEN, PATH) is None


async def test_signed_url_sem_a_chave_esperada_degrada_para_none():
    st, _ = _storage(_Resp(200, {"outra": "coisa"}))
    assert await st.signed_url(TOKEN, PATH) is None


async def test_init_storage_e_idempotente_e_close_libera_o_client():
    settings = type(
        "S", (), {"supabase_url": "https://proj.supabase.co", "anexos_bucket": "b",
                  "signed_url_ttl": 60}
    )()
    primeiro = await storage_mod.init_storage(settings)  # type: ignore[arg-type]
    assert await storage_mod.init_storage(settings) is primeiro  # type: ignore[arg-type]
    assert storage_mod.get_storage() is primeiro
    await storage_mod.close_storage()
    assert storage_mod._storage is None
    await storage_mod.close_storage()  # segunda vez é no-op, não explode


def test_get_storage_sem_init_explica_o_motivo():
    with pytest.raises(RuntimeError, match="não inicializado"):
        storage_mod.get_storage()


async def test_ensure_storage_sem_supabase_configurado_devolve_none(monkeypatch):
    monkeypatch.setattr(
        "app.config.get_settings", lambda: type("S", (), {"supabase_url": ""})()
    )
    assert await storage_mod.ensure_storage() is None


async def test_ensure_storage_inicializa_quando_ha_supabase(monkeypatch):
    settings = type(
        "S", (), {"supabase_url": "https://proj.supabase.co", "anexos_bucket": "b",
                  "signed_url_ttl": 60}
    )()
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    st = await storage_mod.ensure_storage()
    assert isinstance(st, AnexosStorage)
    assert await storage_mod.ensure_storage() is st  # já inicializado: reusa
    await storage_mod.close_storage()

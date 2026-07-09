"""Testes da rota de resposta com anexos (Fase 3 — Storage).

Auth e repositório são fakes; o Storage é substituído por um duplo que registra
uploads e devolve signed URLs determinísticas (sem rede/Supabase).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.storage as storage_mod
from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.session import ACCESS_COOKIE
from app.main import app
from app.repositories.chamados import get_chamados_repo

UID = "11111111-1111-1111-1111-111111111111"
EMPRESA = "22222222-2222-2222-2222-222222222222"
PDF = b"%PDF-1.4\n" + b"conteudo de teste " * 4


def _cliente() -> CurrentUser:
    return CurrentUser(
        id=UID, email="c@e.com", role="CLIENTE",
        claims={"sub": UID, "app_metadata": {"role": "CLIENTE"}},
    )


class FakeRepo:
    def __init__(self):
        self._msgs: list[dict] = []

    async def perfil(self, claims):
        return {"id": UID, "nome": "Cliente", "role": "CLIENTE", "empresa_id": EMPRESA}

    async def obter(self, claims, chamado_id):
        return {
            "id": chamado_id, "codigo": "BOND-2026-00001", "titulo": "T", "descricao": "D",
            "status": "EM_ATENDIMENTO", "prioridade": "ALTA", "cliente_id": UID,
            "categoria": "Suporte", "cliente_nome": "Cliente",
            "created_at": datetime(2026, 6, 30, tzinfo=timezone.utc),
            "limite_resposta": None, "limite_resolucao": None, "resolvido_em": None,
            "avaliacao_nota": None, "avaliacao_comentario": None, "avaliacao_em": None,
        }

    async def mensagens(self, claims, chamado_id):
        return list(self._msgs)

    async def observadores(self, claims, chamado_id):
        return []

    async def usuarios_para_copia(self, claims, *, excluir_id=None):
        return []

    async def adicionar_mensagem(self, claims, chamado_id, *, remetente_id, conteudo, anexos=None):
        self._msgs.append(
            {
                "id": "m1", "conteudo": conteudo, "is_interna": False,
                "created_at": datetime.now(timezone.utc), "anexos": anexos or [],
                "remetente_nome": "Cliente", "remetente_role": "CLIENTE",
            }
        )
        return {"id": "m1", "created_at": datetime.now(timezone.utc)}


class FakeStorage:
    def __init__(self):
        self.uploads: list[tuple] = []

    @staticmethod
    def path(empresa_id, chamado_id, nome_objeto):
        return f"{empresa_id}/{chamado_id}/{nome_objeto}"

    async def upload(self, token, path, conteudo, mime):
        self.uploads.append((path, mime, len(conteudo)))

    async def signed_url(self, token, path):
        return f"https://signed.example/{path}?tok=abc"


@contextmanager
def portal_client(repo: FakeRepo, storage: FakeStorage | None):
    app.dependency_overrides[get_current_user] = _cliente
    app.dependency_overrides[get_chamados_repo] = lambda: repo
    prev = storage_mod._storage
    storage_mod._storage = storage
    try:
        with TestClient(app, base_url="https://testserver") as client:
            client.cookies.set(ACCESS_COOKIE, "faketoken")
            yield client
    finally:
        storage_mod._storage = prev
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_chamados_repo, None)


def _csrf(client: TestClient, chamado_id="aaa") -> str:
    client.get(f"/portal/chamados/{chamado_id}")
    return client.cookies.get("csrf_token")


def test_mensagem_texto_sem_storage_funciona():
    repo = FakeRepo()
    with portal_client(repo, storage=None) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/mensagens",
            data={"conteudo": "Só texto"},
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 200  # segue o 303 -> GET detalhe
    assert "Só texto" in resp.text
    assert repo._msgs[0]["anexos"] == []


def test_mensagem_vazia_sem_anexo_rejeitada():
    repo = FakeRepo()
    with portal_client(repo, storage=None) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/mensagens",
            data={"conteudo": "   "},
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert repo._msgs == []


def test_upload_valido_persiste_e_gera_signed_url():
    repo = FakeRepo()
    storage = FakeStorage()
    with portal_client(repo, storage) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/mensagens",
            data={"conteudo": "Veja o anexo"},
            files={"arquivos": ("relatorio.pdf", PDF, "application/pdf")},
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 200
    # Upload chegou ao Storage com o path tenant-scoped.
    assert len(storage.uploads) == 1
    path, mime, _ = storage.uploads[0]
    assert path.startswith(f"{EMPRESA}/aaa/")
    assert mime == "application/pdf"
    # Metadado persistido + signed URL renderizada no detalhe.
    assert repo._msgs[0]["anexos"][0]["nome"] == "relatorio.pdf"
    assert "signed.example" in resp.text


def test_upload_tipo_invalido_retorna_422():
    repo = FakeRepo()
    storage = FakeStorage()
    with portal_client(repo, storage) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/mensagens",
            data={"conteudo": "x"},
            files={"arquivos": ("virus.exe", PDF, "application/octet-stream")},
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 422
    assert storage.uploads == []       # nada enviado
    assert repo._msgs == []            # nada persistido

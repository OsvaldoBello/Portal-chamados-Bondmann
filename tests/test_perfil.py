"""Testes da foto de perfil (Fase 7) — sem banco/Storage reais.

O envio ao Storage (`app.avatar_storage.enviar_avatar`) é substituído por um
fake via monkeypatch — os testes cobrem rota/validação/persistência do path,
não a chamada de rede em si (isso é integração contra o Supabase live).
"""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.repositories.chamados import get_chamados_repo

UID = "33333333-3333-3333-3333-333333333333"
EMPRESA = "22222222-2222-2222-2222-222222222222"

# PNG 3x5 (não-quadrado) válido e decodificável pelo Pillow — de propósito
# retangular para exercitar o recorte automático em quadrado (app/avatar_storage.py).
_PNG_3X5 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000030000000508020000000f13c1"
    "f50000001349444154789c633c6164c400064c108a200b003a0201364f97f8f4"
    "0000000049454e44ae426082"
)


def _user() -> CurrentUser:
    return CurrentUser(id=UID, email="user@bond.com", role="CLIENTE",
                       claims={"sub": UID, "app_metadata": {"role": "CLIENTE"}})


class FakeRepo:
    def __init__(self, telefone=""):
        self.avatar_atualizado = None
        self.telefone = telefone
        self.telefones_salvos: list[str] = []

    async def perfil(self, claims):
        return {"id": UID, "nome": "Fulano", "role": "CLIENTE", "empresa_id": EMPRESA,
                "departamento": "TI", "avatar_path": None, "avatar_atualizado_em": None,
                "telefone": self.telefone}

    async def atualizar_avatar(self, claims, *, avatar_path):
        self.avatar_atualizado = avatar_path

    async def atualizar_telefone(self, claims, *, telefone):
        self.telefones_salvos.append(telefone)
        self.telefone = telefone


@contextmanager
def perfil_client(repo):
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_chamados_repo] = lambda: repo
    try:
        with TestClient(app, base_url="https://testserver") as c:
            # cookie de acesso necessário para o upload (app.anexos.access_token)
            c.cookies.set("sb_access", "fake-jwt")
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_chamados_repo, None)


def _csrf(c):
    c.get("/perfil")
    return c.cookies.get("csrf_token")


def test_pagina_de_perfil_renderiza():
    with perfil_client(FakeRepo()) as c:
        r = c.get("/perfil")
    assert r.status_code == 200
    assert "Meu perfil" in r.text
    assert 'action="/perfil"' in r.text


def test_upload_de_avatar_valido_salva_path(monkeypatch):
    import app.routes.perfil as perfil_routes

    chamadas = []

    async def _fake_enviar(token, path, conteudo, mime):
        chamadas.append((token, path, conteudo, mime))

    monkeypatch.setattr(perfil_routes, "enviar_avatar", _fake_enviar)

    repo = FakeRepo()
    with perfil_client(repo) as c:
        t = _csrf(c)
        r = c.post(
            "/perfil",
            files={"arquivo": ("foto.png", BytesIO(_PNG_3X5), "image/png")},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/perfil"
    assert repo.avatar_atualizado == f"{UID}/avatar.png"
    assert len(chamadas) == 1

    # A foto 3x5 (retangular) enviada deve ter sido recortada num quadrado e
    # normalizada para PNG antes de ir ao Storage (app/avatar_storage.py).
    from PIL import Image

    _, path, conteudo, mime = chamadas[0]
    assert path == f"{UID}/avatar.png"
    assert mime == "image/png"
    with Image.open(BytesIO(conteudo)) as img:
        assert img.width == img.height


def test_upload_tipo_invalido_e_rejeitado(monkeypatch):
    import app.routes.perfil as perfil_routes

    async def _fake_enviar(*a, **k):
        raise AssertionError("não deveria chegar a enviar ao Storage")

    monkeypatch.setattr(perfil_routes, "enviar_avatar", _fake_enviar)

    repo = FakeRepo()
    with perfil_client(repo) as c:
        t = _csrf(c)
        r = c.post(
            "/perfil",
            files={"arquivo": ("documento.pdf", BytesIO(b"%PDF-1.4 conteudo"), "application/pdf")},
            headers={"X-CSRF-Token": t},
        )
    assert r.status_code == 422
    assert "JPG ou PNG" in r.text
    assert repo.avatar_atualizado is None


def test_post_sem_arquivo_selecionado_e_rejeitado_antes_da_rota():
    """Input de arquivo vazio: o navegador manda a parte sem `filename`, o
    Starlette a entrega como string e o FastAPI barra na validação de
    `UploadFile` — 422 antes de qualquer efeito colateral. A guarda
    `if not arquivo.filename` na rota é defesa em profundidade para o caso de
    alguém montar o multipart na mão."""
    repo = FakeRepo()
    with perfil_client(repo) as c:
        t = _csrf(c)
        r = c.post(
            "/perfil",
            files={"arquivo": ("", BytesIO(b""), "image/png")},
            headers={"X-CSRF-Token": t},
        )
    assert r.status_code == 422
    assert repo.avatar_atualizado is None


def test_upload_sem_sessao_valida_avisa_em_vez_de_500(monkeypatch):
    """Sem o cookie de acesso não há JWT para o Storage (que roda com o token do
    usuário, nunca service_role) — a pessoa precisa saber que é sessão, não foto."""
    import app.routes.perfil as perfil_routes

    async def _fake_enviar(*a, **k):
        raise AssertionError("não deveria chegar ao Storage sem token")

    monkeypatch.setattr(perfil_routes, "enviar_avatar", _fake_enviar)

    repo = FakeRepo()
    with perfil_client(repo) as c:
        t = _csrf(c)
        c.cookies.delete("sb_access")
        r = c.post(
            "/perfil",
            files={"arquivo": ("foto.png", BytesIO(_PNG_3X5), "image/png")},
            headers={"X-CSRF-Token": t},
        )
    assert r.status_code == 422
    assert "Sessão expirada" in r.text
    assert repo.avatar_atualizado is None


def test_falha_do_storage_nao_grava_path_no_perfil(monkeypatch):
    """Se o Storage recusa, `perfis.avatar_path` não pode apontar para um objeto
    que não existe (o card mostraria imagem quebrada para sempre)."""
    import app.routes.perfil as perfil_routes

    async def _fake_enviar(*a, **k):
        raise perfil_routes.AvatarStorageError("bucket fora do ar")

    monkeypatch.setattr(perfil_routes, "enviar_avatar", _fake_enviar)

    repo = FakeRepo()
    with perfil_client(repo) as c:
        t = _csrf(c)
        r = c.post(
            "/perfil",
            files={"arquivo": ("foto.png", BytesIO(_PNG_3X5), "image/png")},
            headers={"X-CSRF-Token": t},
        )
    assert r.status_code == 422
    assert "Falha ao enviar a foto" in r.text
    assert repo.avatar_atualizado is None


# --------------------------------------------------------------------------
# Telefone de contato no perfil (2026-07-29, migration 0062)
# --------------------------------------------------------------------------
def test_perfil_sem_telefone_avisa_que_a_abertura_vai_pedir():
    with perfil_client(FakeRepo()) as c:
        r = c.get("/perfil")
    assert r.status_code == 200
    assert 'action="/perfil/telefone"' in r.text
    assert "Ainda não cadastrado" in r.text


def test_perfil_com_telefone_preenche_o_campo():
    with perfil_client(FakeRepo(telefone="(51) 98167-0729")) as c:
        r = c.get("/perfil")
    assert r.status_code == 200
    assert 'value="(51) 98167-0729"' in r.text
    assert "Ainda não cadastrado" not in r.text


def test_salvar_telefone_valido_grava_e_redireciona():
    repo = FakeRepo()
    with perfil_client(repo) as c:
        t = _csrf(c)
        r = c.post(
            "/perfil/telefone",
            data={"telefone": "(51) 98167-0729"},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"].startswith("/perfil")
    assert repo.telefones_salvos == ["(51) 98167-0729"]


def test_salvar_telefone_invalido_nao_grava():
    repo = FakeRepo()
    with perfil_client(repo) as c:
        t = _csrf(c)
        r = c.post(
            "/perfil/telefone",
            data={"telefone": "123"},
            headers={"X-CSRF-Token": t},
        )
    assert r.status_code == 422
    assert "contato válido" in r.text
    assert repo.telefones_salvos == []
    # O que foi digitado volta no campo, para a pessoa corrigir em vez de redigitar.
    assert 'value="123"' in r.text

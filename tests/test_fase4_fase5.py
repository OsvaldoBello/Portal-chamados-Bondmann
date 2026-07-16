"""Testes das entregas das Fases 4 e 5.

Fase 4: rota do sino (/notificacoes) e config do Realtime (/realtime/config).
Fase 5: fluxo de redefinição de senha (validação) e páginas de erro 403/404.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.repositories.chamados import get_chamados_repo

UID = "11111111-1111-1111-1111-111111111111"


def _user(role="CLIENTE"):
    return lambda: CurrentUser(
        id=UID, email="func@bond.com", role=role,
        claims={"sub": UID, "app_metadata": {"role": role}},
    )


class FakeNotif:
    async def notificacoes(self, claims, *, limite=6):
        agora = datetime.now(UTC)
        return [
            {"id": "a1", "codigo": "BOND-2026-00042", "titulo": "Impressora travando",
             "status": "NOVO", "created_at": agora - timedelta(hours=1),
             "limite_resolucao": agora + timedelta(hours=20), "resolvido_em": None,
             "avaliacao_nota": None, "meu": True},
        ]


@contextmanager
def client(*, user=None, repo=None):
    if user:
        app.dependency_overrides[get_current_user] = user
    if repo is not None:
        app.dependency_overrides[get_chamados_repo] = lambda: repo
    try:
        with TestClient(app, base_url="https://testserver") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_chamados_repo, None)


# --------------------------------------------------------------------------
# Fase 4 — sino de notificações
# --------------------------------------------------------------------------
def test_notificacoes_render_itens():
    with client(user=_user(), repo=FakeNotif()) as c:
        r = c.get("/notificacoes")
    assert r.status_code == 200
    assert "BOND-2026-00042" in r.text
    assert "Impressora travando" in r.text


def test_notificacoes_exige_login():
    with TestClient(app, base_url="https://testserver") as c:
        # Sem override de auth e sem cookie → 401 (não HTML → JSON).
        assert c.get("/notificacoes").status_code == 401


def test_realtime_config_autenticado_retorna_json():
    with client(user=_user()) as c:
        r = c.get("/realtime/config")
    assert r.status_code == 200
    # Em teste o Supabase não está configurado → objeto vazio (degrada no cliente).
    assert isinstance(r.json(), dict)


def test_realtime_config_exige_login():
    with TestClient(app, base_url="https://testserver") as c:
        assert c.get("/realtime/config").status_code == 401


# --------------------------------------------------------------------------
# Fase 5 — redefinição de senha (validação, sem tocar no Supabase)
# --------------------------------------------------------------------------
def test_esqueci_senha_form():
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/esqueci-senha")
    assert r.status_code == 200
    assert "Esqueci minha senha" in r.text
    assert 'action="/esqueci-senha"' in r.text


def test_redefinir_senha_form():
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/redefinir-senha?email=func%40bond.com")
    assert r.status_code == 200
    assert "Criar nova senha" in r.text
    assert "func@bond.com" in r.text


def test_redefinir_senha_senhas_diferentes():
    with TestClient(app, base_url="https://testserver") as c:
        c.get("/redefinir-senha")
        token = c.cookies.get("csrf_token")
        r = c.post(
            "/redefinir-senha",
            data={"email": "func@bond.com", "codigo": "123456",
                  "senha": "supersecret1", "senha2": "outrasenha1", "csrf_token": token},
            headers={"X-CSRF-Token": token},
        )
    assert r.status_code == 400
    assert "não coincidem" in r.text


def test_redefinir_senha_curta():
    with TestClient(app, base_url="https://testserver") as c:
        c.get("/redefinir-senha")
        token = c.cookies.get("csrf_token")
        r = c.post(
            "/redefinir-senha",
            data={"email": "func@bond.com", "codigo": "123456",
                  "senha": "123", "senha2": "123", "csrf_token": token},
            headers={"X-CSRF-Token": token},
        )
    assert r.status_code == 400
    assert "ao menos 8" in r.text


# --------------------------------------------------------------------------
# Fase 5 — páginas de erro 403/404
# --------------------------------------------------------------------------
def test_404_pagina_html():
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/rota-que-nao-existe", headers={"accept": "text/html"})
    assert r.status_code == 404
    assert "Página não encontrada" in r.text
    assert "404" in r.text


def test_404_json_quando_nao_html():
    with TestClient(app, base_url="https://testserver") as c:
        r = c.get("/rota-que-nao-existe", headers={"accept": "application/json"})
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")

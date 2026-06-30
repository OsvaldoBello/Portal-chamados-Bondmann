"""Smoke tests da app (Fase 1 DoD): /health e CSRF nas mutações."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_ok():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_login_page_renders_and_sets_csrf_cookie():
    with TestClient(app) as client:
        resp = client.get("/login")
    assert resp.status_code == 200
    assert "csrf_token" in resp.cookies
    assert "Entrar" in resp.text


def test_mutation_without_csrf_is_forbidden():
    with TestClient(app) as client:
        # Sem cookie/header CSRF -> 403 antes de qualquer lógica de auth.
        resp = client.post("/login", data={"email": "a@b.com", "password": "x"})
    assert resp.status_code == 403


def test_security_headers_present():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "default-src 'self'" in resp.headers["Content-Security-Policy"]

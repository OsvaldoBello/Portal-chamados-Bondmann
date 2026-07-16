"""Smoke tests da app (Fase 1 DoD): /health e CSRF nas mutações.

Também cobre o gate de diagnóstico de /health/ready e /health/config em
produção (Sprint 1 / item 1.2, M3)."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app import metrics
from app.config import Settings
from app.main import app


def setup_function() -> None:
    metrics.reset()


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
    )
    base.update(overrides)
    return Settings(**base)


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


def test_ready_e_config_livres_fora_de_producao():
    # `_ensure_pool` é mockado para não tocar o banco real (a rota usa o
    # singleton `get_settings()` do app.db, independente do patch abaixo).
    with patch("app.routes.health.get_settings", return_value=_settings(environment="development")):
        with patch("app.routes.health._ensure_pool", AsyncMock(side_effect=RuntimeError("sem DB no teste"))):
            with TestClient(app) as client:
                resp_config = client.get("/health/config")
                resp_ready = client.get("/health/ready")
    assert resp_config.status_code == 200
    assert resp_ready.status_code == 503  # sem DB no teste, mas não é 403 (fora de produção é livre)
    assert "sem DB no teste" in resp_ready.json()["db_error"]


def test_config_em_producao_sem_token_configurado_nega():
    with patch("app.routes.health.get_settings", return_value=_settings(environment="production", diagnostics_token="")):
        with TestClient(app) as client:
            resp = client.get("/health/config")
    assert resp.status_code == 403


def test_config_em_producao_com_token_errado_nega():
    settings = _settings(environment="production", diagnostics_token="segredo-diagnostico")
    with patch("app.routes.health.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.get("/health/config", headers={"X-Diagnostics-Token": "chute-errado"})
    assert resp.status_code == 403


def test_config_em_producao_com_token_certo_libera():
    settings = _settings(environment="production", diagnostics_token="segredo-diagnostico")
    with patch("app.routes.health.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.get("/health/config", headers={"X-Diagnostics-Token": "segredo-diagnostico"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["environment"] == "production"


def test_ready_em_producao_sem_token_nega():
    with patch("app.routes.health.get_settings", return_value=_settings(environment="production", diagnostics_token="")):
        with TestClient(app) as client:
            resp = client.get("/health/ready")
    assert resp.status_code == 403


def test_ready_em_producao_com_token_nao_expoe_detalhe_do_erro():
    settings = _settings(environment="production", diagnostics_token="segredo-diagnostico")
    with patch("app.routes.health.get_settings", return_value=settings):
        with patch("app.routes.health._ensure_pool", AsyncMock(side_effect=RuntimeError("detalhe interno sensível"))):
            with TestClient(app) as client:
                resp = client.get("/health/ready", headers={"X-Diagnostics-Token": "segredo-diagnostico"})
    assert resp.status_code == 503
    body = resp.json()
    assert "db_error" not in body
    assert "detalhe interno sensível" not in resp.text
    assert body["status"] == "degraded"


def test_metrics_livre_fora_de_producao_e_reflete_snapshot():
    # Mesmo gate de diagnóstico de /health/ready e /health/config (Sprint 2 /
    # item 2.6): fora de produção não exige token.
    metrics.registrar_request("/workspace/fila/fragmento", 304, 3.0, is_polling=True)
    with patch("app.routes.health.get_settings", return_value=_settings(environment="development")):
        with TestClient(app) as client:
            resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["requests_total"] >= 1  # a própria chamada a /metrics também é contada
    assert body["polling_304"]["hits"] == 1
    assert "db_pool" in body
    assert body["sentry_enabled"] is False


def test_metrics_em_producao_sem_token_nega():
    with patch("app.routes.health.get_settings", return_value=_settings(environment="production", diagnostics_token="")):
        with TestClient(app) as client:
            resp = client.get("/metrics")
    assert resp.status_code == 403


def test_metrics_em_producao_com_token_certo_libera():
    settings = _settings(environment="production", diagnostics_token="segredo-diagnostico")
    with patch("app.routes.health.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.get("/metrics", headers={"X-Diagnostics-Token": "segredo-diagnostico"})
    assert resp.status_code == 200


def test_metrics_reporta_sentry_enabled_quando_dsn_configurada():
    settings = _settings(environment="development", sentry_dsn="https://exemplo@sentry.example/1")
    with patch("app.routes.health.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.get("/metrics")
    assert resp.json()["sentry_enabled"] is True


def test_exception_nao_tratada_e_capturada_no_sentry_com_request_id():
    """A rota de erro sempre existe: registrada uma vez no import do módulo de
    teste (FastAPI não permite registrar rota condicionalmente sem reiniciar
    a app), então usamos uma rota já existente que dispara erro 500 via
    dependência quebrada — aqui, /health/ready com uma falha inesperada
    (não ``Exception`` capturada pela própria rota, mas um erro de fato não
    tratado em nenhum lugar) simulada via patch em ``_diagnostico_autorizado``
    para forçar o handler central."""
    with patch("app.main.sentry_sdk.Scope.capture_exception") as capture:
        with patch("app.routes.health._diagnostico_autorizado", side_effect=RuntimeError("boom")):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get("/health/ready")
    assert resp.status_code == 500
    assert resp.json()["request_id"]
    capture.assert_called_once()
    exc_arg = capture.call_args.args[0]
    assert isinstance(exc_arg, RuntimeError)

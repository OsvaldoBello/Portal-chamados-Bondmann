"""Testes de MFA/TOTP — item 3.3 (Sprint 3), Fase 1 da Seção 3.4.1.

Cobre o que o item pede: enroll, verify, sessão **aal1 barrada** em rota ADMIN,
**aal2 liberada**, e usuário **sem MFA** (comportamento de transição = nudge).

Nenhum teste toca a rede: o enforcement é puro (lê `aal`/`app_metadata.mfa_enabled`
dos claims) e as operações de GoTrue em `app.auth.mfa` são trocadas por fakes via
monkeypatch — é justamente por isso que o espelho de claim existe (ver docstring
de `app/auth/mfa.py`).
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.auth import mfa
from app.auth.dependencies import (
    CurrentUser,
    MfaChallengeRequired,
    enforce_admin_mfa,
    get_current_user,
)
from app.auth.routes import _tem_fator_verificado
from app.auth.session import SessionTokens
from app.main import app
from app.repositories.admin import get_admin_repo
from app.repositories.chamados import get_chamados_repo

UID = "88888888-8888-8888-8888-888888888888"


def _user(role="ADMIN", *, aal=None, mfa_enabled=None) -> CurrentUser:
    app_meta: dict = {"role": role}
    if mfa_enabled is not None:
        app_meta["mfa_enabled"] = mfa_enabled
    claims: dict = {"sub": UID, "app_metadata": app_meta}
    if aal is not None:
        claims["aal"] = aal
    return CurrentUser(id=UID, email="admin@bond.com", role=role, claims=claims)


# ---------------------------------------------------------------------------
# Enforcement (puro — sem app, sem rede)
# ---------------------------------------------------------------------------

def test_admin_sem_mfa_recebe_nudge_e_nao_e_bloqueado():
    # Fase 1 = "opcional com aviso" (decisão do gestor): quem nunca ativou entra.
    assert enforce_admin_mfa(_user("ADMIN")) is True


def test_admin_com_mfa_em_aal1_exige_step_up():
    with pytest.raises(MfaChallengeRequired):
        enforce_admin_mfa(_user("ADMIN", aal="aal1", mfa_enabled=True))


def test_admin_com_mfa_em_aal2_passa_sem_nudge():
    assert enforce_admin_mfa(_user("ADMIN", aal="aal2", mfa_enabled=True)) is False


def test_aal_ausente_no_token_vale_como_aal1():
    # Fail-safe: a ausência do claim nunca pode contar como "MFA verificado".
    with pytest.raises(MfaChallengeRequired):
        enforce_admin_mfa(_user("ADMIN", mfa_enabled=True))


@pytest.mark.parametrize("role", ["OPERADOR", "CLIENTE"])
def test_nao_admin_fica_fora_do_enforcement_nesta_fatia(role):
    # "NÃO faça: obrigar MFA para OPERADOR/CLIENTE nesta fatia" — nem bloqueio
    # nem nudge, mesmo que a conta tenha MFA e a sessão esteja em aal1.
    assert enforce_admin_mfa(_user(role, aal="aal1", mfa_enabled=True)) is False


# ---------------------------------------------------------------------------
# Enforcement na rota ADMIN de verdade (via admin_context)
# ---------------------------------------------------------------------------

class _FakePerfilRepo:
    """Só o `perfil` — é o que o `admin_context` consome do ChamadosRepo."""

    def __init__(self, role="ADMIN", departamento="TI", is_ti=True):
        self._p = {"id": UID, "nome": "Fulano", "role": role, "departamento": departamento,
                   "is_ti": is_ti, "empresa_id": "e1"}

    async def perfil(self, claims):
        return self._p


class _FakeAdminRepo:
    """Superfície mínima que a rota `/admin` (dashboard) chama."""

    async def kpis(self, claims, **kw):
        return {"total": 1, "abertos": 1, "resolvidos": 0, "resolvidos_no_prazo": 0,
                "conformidade_sla": 0.0, "csat_media": 0.0, "csat_respostas": 0,
                "tma_horas": 0.0, "tma_seg": 0}

    async def por_status(self, claims, **kw):
        return {"NOVO": 1}

    async def csat_distribuicao(self, claims, **kw):
        return {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    async def por_departamento(self, claims, **kw):
        return []

    async def por_setor(self, claims, **kw):
        return []

    async def produtividade(self, claims, **kw):
        return []

    async def avaliacoes_recentes(self, claims, **kw):
        return []


@contextmanager
def _client(user: CurrentUser | None = None):
    app.dependency_overrides[get_current_user] = lambda: user or _user()
    app.dependency_overrides[get_admin_repo] = lambda: _FakeAdminRepo()
    app.dependency_overrides[get_chamados_repo] = lambda: _FakePerfilRepo()
    try:
        with TestClient(app, base_url="https://testserver") as c:
            c.cookies.set("sb_access", "access-token-fake")
            c.cookies.set("sb_refresh", "refresh-token-fake")
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_admin_repo, None)
        app.dependency_overrides.pop(get_chamados_repo, None)


def _csrf(c) -> str:
    # /login renderiza e seta o cookie CSRF sem exigir sessão nem rede.
    c.get("/login")
    return c.cookies.get("csrf_token")


def test_rota_admin_em_aal1_com_mfa_redireciona_para_verificacao():
    with _client(_user("ADMIN", aal="aal1", mfa_enabled=True)) as c:
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/mfa/verify"


def test_rota_admin_em_aal1_com_mfa_via_htmx_usa_hx_redirect():
    # Fragmento HTMX não sabe navegar sozinho — mesmo tratamento do 401.
    with _client(_user("ADMIN", aal="aal1", mfa_enabled=True)) as c:
        r = c.get("/admin", headers={"HX-Request": "true"}, follow_redirects=False)
        assert r.status_code == 204
        assert r.headers["HX-Redirect"] == "/mfa/verify"


def test_rota_admin_em_aal2_libera_o_painel():
    with _client(_user("ADMIN", aal="aal2", mfa_enabled=True)) as c:
        r = c.get("/admin")
        assert r.status_code == 200
        assert "Ativar MFA" not in r.text  # sessão verificada → sem nudge


def test_rota_admin_sem_mfa_entra_com_aviso_transicao():
    # Comportamento de transição: não bloqueia, só avisa.
    with _client(_user("ADMIN")) as c:
        r = c.get("/admin")
        assert r.status_code == 200
        assert "Ativar MFA" in r.text


def test_operador_do_ti_no_painel_nao_recebe_enforcement_nem_nudge():
    # OPERADOR do TI também entra no painel; MFA não é imposto a ele nesta fatia.
    app.dependency_overrides[get_current_user] = lambda: _user("OPERADOR", aal="aal1")
    app.dependency_overrides[get_admin_repo] = lambda: _FakeAdminRepo()
    app.dependency_overrides[get_chamados_repo] = lambda: _FakePerfilRepo(role="OPERADOR")
    try:
        with TestClient(app, base_url="https://testserver") as c:
            r = c.get("/admin")
            assert r.status_code == 200
            assert "Ativar MFA" not in r.text
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

def test_enroll_exibe_qr_e_segredo_uma_vez(monkeypatch):
    async def fake_enroll(tokens):
        assert tokens.access_token == "access-token-fake"
        return mfa.EnrollTotp(
            factor_id="fator-1",
            qr_code="data:image/svg+xml;charset=utf-8,%3Csvg%3E%3C/svg%3E",
            secret="JBSWY3DPEHPK3PXP",
            uri="otpauth://totp/Portal%20Bondmann",
        )

    monkeypatch.setattr(mfa, "iniciar_enroll", fake_enroll)
    with _client() as c:
        t = _csrf(c)
        r = c.post("/mfa/enroll", headers={"X-CSRF-Token": t})
        assert r.status_code == 200
        assert "JBSWY3DPEHPK3PXP" in r.text          # segredo para entrada manual
        assert "data:image/svg+xml" in r.text        # QR inline (CSP img-src data:)
        assert 'value="fator-1"' in r.text           # factor_id segue para o confirmar


def test_enroll_confirmar_ativa_sobe_sessao_e_espelha_claim(monkeypatch):
    vistos: dict = {}

    async def fake_confirmar(tokens, factor_id, codigo):
        vistos["confirmar"] = (factor_id, codigo)
        return SessionTokens("access-aal2", "refresh-novo")

    async def fake_marcar(user_id, habilitado):
        vistos["marcar"] = (user_id, habilitado)

    monkeypatch.setattr(mfa, "confirmar", fake_confirmar)
    monkeypatch.setattr(mfa, "marcar_mfa_habilitado", fake_marcar)
    with _client() as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/enroll/confirmar",
            data={"factor_id": "fator-1", "codigo": "123 456"},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/mfa?ok=1"
        # Espaços do código são normalizados antes de ir ao GoTrue.
        assert vistos["confirmar"] == ("fator-1", "123456")
        # O espelho é o que permite o enforcement ler MFA sem chamar o GoTrue.
        assert vistos["marcar"] == (UID, True)
        assert "access-aal2" in r.headers["set-cookie"]


def test_enroll_confirmar_com_codigo_invalido_nao_ativa(monkeypatch):
    async def fake_confirmar(tokens, factor_id, codigo):
        raise mfa.MfaErro("verify falhou: AuthApiError")

    marcou = []

    async def fake_marcar(user_id, habilitado):
        marcou.append(user_id)

    monkeypatch.setattr(mfa, "confirmar", fake_confirmar)
    monkeypatch.setattr(mfa, "marcar_mfa_habilitado", fake_marcar)
    with _client() as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/enroll/confirmar",
            data={"factor_id": "fator-1", "codigo": "000000"},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "Código inválido" in r.text
        assert marcou == []  # não marca a flag se a verificação falhou


# ---------------------------------------------------------------------------
# Verificação (step-up de sessão aal1)
# ---------------------------------------------------------------------------

def test_verify_com_codigo_valido_sobe_para_aal2_e_vai_para_home(monkeypatch):
    async def fake_fator(tokens):
        return "fator-1"

    async def fake_confirmar(tokens, factor_id, codigo):
        return SessionTokens("access-aal2", "refresh-novo")

    monkeypatch.setattr(mfa, "fator_verificado_id", fake_fator)
    monkeypatch.setattr(mfa, "confirmar", fake_confirmar)
    with _client(_user("ADMIN", aal="aal1", mfa_enabled=True)) as c:
        t = _csrf(c)
        r = c.post("/mfa/verify", data={"codigo": "123456"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/workspace"  # home_for(ADMIN)
        assert "access-aal2" in r.headers["set-cookie"]


def test_verify_com_codigo_invalido_reexibe_o_form(monkeypatch):
    async def fake_fator(tokens):
        return "fator-1"

    async def fake_confirmar(tokens, factor_id, codigo):
        raise mfa.MfaErro("verify falhou: AuthApiError")

    monkeypatch.setattr(mfa, "fator_verificado_id", fake_fator)
    monkeypatch.setattr(mfa, "confirmar", fake_confirmar)
    with _client(_user("ADMIN", aal="aal1", mfa_enabled=True)) as c:
        t = _csrf(c)
        r = c.post("/mfa/verify", data={"codigo": "000000"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
        assert r.status_code == 400
        assert "Código inválido" in r.text


def test_verify_sem_fator_manda_para_o_hub(monkeypatch):
    # Ex.: TI resetou o MFA no meio do fluxo — não há o que verificar.
    async def fake_fator(tokens):
        return None

    monkeypatch.setattr(mfa, "fator_verificado_id", fake_fator)
    with _client(_user("ADMIN", aal="aal1", mfa_enabled=True)) as c:
        t = _csrf(c)
        r = c.post("/mfa/verify", data={"codigo": "123456"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/mfa"


def test_verify_em_sessao_ja_verificada_volta_para_home():
    # Nada a fazer em aal2 — evita pedir código de novo sem motivo.
    with _client(_user("ADMIN", aal="aal2", mfa_enabled=True)) as c:
        r = c.get("/mfa/verify", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/workspace"


# ---------------------------------------------------------------------------
# Step-up no login (fatores vêm na própria resposta do GoTrue)
# ---------------------------------------------------------------------------

class _FakeFator:
    def __init__(self, status):
        self.status = status


def test_tem_fator_verificado_ignora_fator_nao_verificado():
    class _U:
        factors = [_FakeFator("unverified")]

    # Enroll começado e abandonado não deve mandar ninguém ao step-up.
    assert _tem_fator_verificado(_U()) is False


def test_tem_fator_verificado_detecta_fator_ativo():
    class _U:
        factors = [_FakeFator("unverified"), _FakeFator("verified")]

    assert _tem_fator_verificado(_U()) is True


def test_tem_fator_verificado_sem_fatores():
    class _U:
        factors = None

    assert _tem_fator_verificado(_U()) is False

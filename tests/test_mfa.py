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
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.auth import mfa, mfa_email, mfa_email_stepup, mfa_remember, routes as auth_routes
from app.auth.dependencies import (
    CurrentUser,
    MfaChallengeRequired,
    enforce_admin_mfa,
    get_current_user,
    sessao_mfa_satisfeita,
)
from app.auth.routes import _precisa_step_up, _tem_fator_verificado
from app.auth.session import SessionTokens
from app.config import get_settings
from app.main import app
from app.repositories.admin import get_admin_repo
from app.repositories.chamados import get_chamados_repo

UID = "88888888-8888-8888-8888-888888888888"


def _user(role="ADMIN", *, aal=None, mfa_enabled=None, mfa_email_enabled=None) -> CurrentUser:
    app_meta: dict = {"role": role}
    if mfa_enabled is not None:
        app_meta["mfa_enabled"] = mfa_enabled
    if mfa_email_enabled is not None:
        app_meta["mfa_email_enabled"] = mfa_email_enabled
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
# "Lembrar este dispositivo" por 30 dias (pedido do usuário, 2026-07-27)
# ---------------------------------------------------------------------------

def _cookie_request(token: str | None):
    """Fake mínimo de Request — só `.cookies` é lido por `dispositivo_confiavel`
    (mesmo truque de `tests/test_session.py::_request`, sem framework)."""
    return SimpleNamespace(cookies={mfa_remember.REMEMBER_COOKIE: token} if token else {})


def _token_para(user_id: str) -> str:
    return mfa_remember._serializer(get_settings()).dumps(user_id)


def test_dispositivo_confiavel_aceita_cookie_valido_do_mesmo_usuario():
    token = _token_para(UID)
    assert mfa_remember.dispositivo_confiavel(_cookie_request(token), get_settings(), UID) is True


def test_dispositivo_confiavel_rejeita_cookie_de_outro_usuario():
    # Cookie assinado, mas para outra conta (ex.: troca de usuário no mesmo navegador).
    token = _token_para("11111111-1111-1111-1111-111111111111")
    assert mfa_remember.dispositivo_confiavel(_cookie_request(token), get_settings(), UID) is False


def test_dispositivo_confiavel_sem_cookie():
    assert mfa_remember.dispositivo_confiavel(_cookie_request(None), get_settings(), UID) is False


def test_dispositivo_confiavel_cookie_adulterado():
    assert mfa_remember.dispositivo_confiavel(_cookie_request("lixo.invalido"), get_settings(), UID) is False


def test_dispositivo_confiavel_expira_apos_30_dias(monkeypatch):
    # A promessa é "30 dias", não "para sempre" — cookie fora da janela não vale.
    token = _token_para(UID)
    monkeypatch.setattr(mfa_remember, "MAX_AGE_SEGUNDOS", -1)
    assert mfa_remember.dispositivo_confiavel(_cookie_request(token), get_settings(), UID) is False


def test_enforce_admin_mfa_pula_step_up_com_dispositivo_confiavel():
    # O cookie substitui o passo de digitar o código, mas não eleva a aal2 do
    # JWT — só o enforcement local (`enforce_admin_mfa`) trata como satisfeito.
    request = _cookie_request(_token_para(UID))
    assert enforce_admin_mfa(_user("ADMIN", aal="aal1", mfa_enabled=True), request) is False


def test_enforce_admin_mfa_sem_dispositivo_confiavel_ainda_exige_step_up():
    with pytest.raises(MfaChallengeRequired):
        enforce_admin_mfa(_user("ADMIN", aal="aal1", mfa_enabled=True), _cookie_request(None))


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

    async def tempo_conclusao_distribuicao(self, claims, **kw):
        return {"mesmo_dia": 0, "dia_seguinte": 0, "dois_dias_mais": 0}

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


def test_verify_com_lembrar_dispositivo_marcado_grava_cookie(monkeypatch):
    async def fake_fator(tokens):
        return "fator-1"

    async def fake_confirmar(tokens, factor_id, codigo):
        return SessionTokens("access-aal2", "refresh-novo")

    monkeypatch.setattr(mfa, "fator_verificado_id", fake_fator)
    monkeypatch.setattr(mfa, "confirmar", fake_confirmar)
    with _client(_user("ADMIN", aal="aal1", mfa_enabled=True)) as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/verify",
            data={"codigo": "123456", "lembrar_dispositivo": "true"},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert mfa_remember.REMEMBER_COOKIE in c.cookies
        assert mfa_remember.dispositivo_confiavel(
            _cookie_request(c.cookies[mfa_remember.REMEMBER_COOKIE]), get_settings(), UID
        )


def test_verify_sem_marcar_lembrar_dispositivo_nao_grava_cookie(monkeypatch):
    async def fake_fator(tokens):
        return "fator-1"

    async def fake_confirmar(tokens, factor_id, codigo):
        return SessionTokens("access-aal2", "refresh-novo")

    monkeypatch.setattr(mfa, "fator_verificado_id", fake_fator)
    monkeypatch.setattr(mfa, "confirmar", fake_confirmar)
    with _client(_user("ADMIN", aal="aal1", mfa_enabled=True)) as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/verify",
            data={"codigo": "123456"},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert mfa_remember.REMEMBER_COOKIE not in c.cookies


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


# ---------------------------------------------------------------------------
# POST /login: o redirect para /mfa/verify considera o dispositivo confiável
# (bug reportado pelo usuário, 2026-07-27 — este é o caminho real do 1º acesso
# pós-login, ANTES de qualquer rota passar por `admin_context`/
# `enforce_admin_mfa`; checar o cookie só lá era tarde demais).
# ---------------------------------------------------------------------------

class _FakeGoTrueUser:
    def __init__(self, user_id, *, role="ADMIN", fatores=None):
        self.id = user_id
        self.app_metadata = {"role": role}
        self.factors = fatores or []


class _FakeSession:
    access_token = "access-novo"
    refresh_token = "refresh-novo"


class _FakeSignInResult:
    def __init__(self, user):
        self.user = user
        self.session = _FakeSession()


class _FakeAuth:
    def __init__(self, user):
        self._user = user

    async def sign_in_with_password(self, _creds):
        return _FakeSignInResult(self._user)


class _FakeSupabaseClient:
    def __init__(self, user):
        self.auth = _FakeAuth(user)


def _fake_login(monkeypatch, user):
    async def fake_ensure_supabase():
        return _FakeSupabaseClient(user)

    monkeypatch.setattr(auth_routes, "ensure_supabase", fake_ensure_supabase)


def test_login_com_mfa_ativo_sem_dispositivo_confiavel_vai_para_step_up(monkeypatch):
    _fake_login(monkeypatch, _FakeGoTrueUser(UID, fatores=[_FakeFator("verified")]))
    with _client() as c:
        t = _csrf(c)
        r = c.post(
            "/login", data={"email": "a@b.com", "password": "x"},
            headers={"X-CSRF-Token": t}, follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/mfa/verify"


def test_login_com_dispositivo_confiavel_pula_o_step_up(monkeypatch):
    # Reprodução exata do bug reportado: usuário marcou "lembrar dispositivo",
    # saiu e entrou de novo — não deve ser mandado para /mfa/verify.
    _fake_login(monkeypatch, _FakeGoTrueUser(UID, role="ADMIN", fatores=[_FakeFator("verified")]))
    with _client() as c:
        t = _csrf(c)
        c.cookies.set(mfa_remember.REMEMBER_COOKIE, _token_para(UID))
        r = c.post(
            "/login", data={"email": "a@b.com", "password": "x"},
            headers={"X-CSRF-Token": t}, follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/workspace"


def test_login_com_cookie_de_outro_usuario_ainda_exige_step_up(monkeypatch):
    _fake_login(monkeypatch, _FakeGoTrueUser(UID, fatores=[_FakeFator("verified")]))
    with _client() as c:
        t = _csrf(c)
        c.cookies.set(
            mfa_remember.REMEMBER_COOKIE,
            _token_para("11111111-1111-1111-1111-111111111111"),
        )
        r = c.post(
            "/login", data={"email": "a@b.com", "password": "x"},
            headers={"X-CSRF-Token": t}, follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/mfa/verify"


def test_login_sem_mfa_vai_direto_para_home_independente_do_cookie(monkeypatch):
    _fake_login(monkeypatch, _FakeGoTrueUser(UID, fatores=[]))
    with _client() as c:
        t = _csrf(c)
        r = c.post(
            "/login", data={"email": "a@b.com", "password": "x"},
            headers={"X-CSRF-Token": t}, follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/workspace"


# ---------------------------------------------------------------------------
# MFA por e-mail — GoTrue não tem fator "email" (item 3.3, extensão): a
# "sessão verificada" é um cookie próprio (app/auth/mfa_email_stepup.py), não
# a claim `aal` real. Estes testes cobrem exatamente o gap descrito na
# docstring de app/auth/mfa_email.py: sem eles, um usuário só-com-e-mail
# nunca seria mandado ao step-up nem barrado no /admin.
# ---------------------------------------------------------------------------

def _email_stepup_token_para(user_id: str) -> str:
    return mfa_email_stepup._serializer(get_settings()).dumps(user_id)


def test_email_verificado_aceita_cookie_valido_do_mesmo_usuario():
    token = _email_stepup_token_para(UID)
    request = _cookie_request(None)
    request.cookies = {mfa_email_stepup.EMAIL_STEPUP_COOKIE: token}
    assert mfa_email_stepup.email_verificado(request, get_settings(), UID) is True


def test_email_verificado_rejeita_cookie_de_outro_usuario():
    token = _email_stepup_token_para("11111111-1111-1111-1111-111111111111")
    request = _cookie_request(None)
    request.cookies = {mfa_email_stepup.EMAIL_STEPUP_COOKIE: token}
    assert mfa_email_stepup.email_verificado(request, get_settings(), UID) is False


def test_email_verificado_sem_cookie():
    assert mfa_email_stepup.email_verificado(_cookie_request(None), get_settings(), UID) is False


def test_sessao_mfa_satisfeita_aceita_aal2_sem_precisar_de_cookie():
    assert sessao_mfa_satisfeita(None, _user("ADMIN", aal="aal2")) is True


def test_sessao_mfa_satisfeita_aceita_cookie_de_email_em_aal1():
    request = _cookie_request(None)
    request.cookies = {mfa_email_stepup.EMAIL_STEPUP_COOKIE: _email_stepup_token_para(UID)}
    assert sessao_mfa_satisfeita(request, _user("ADMIN", aal="aal1")) is True


def test_sessao_mfa_satisfeita_sem_aal2_nem_cookie_e_falsa():
    assert sessao_mfa_satisfeita(_cookie_request(None), _user("ADMIN", aal="aal1")) is False


def test_enforce_admin_mfa_com_so_email_habilitado_exige_step_up():
    # ADMIN que só ativou o e-mail (sem TOTP) também precisa do gate.
    with pytest.raises(MfaChallengeRequired):
        enforce_admin_mfa(_user("ADMIN", aal="aal1", mfa_email_enabled=True))


def test_enforce_admin_mfa_com_cookie_de_email_libera_sem_nudge():
    request = _cookie_request(None)
    request.cookies = {mfa_email_stepup.EMAIL_STEPUP_COOKIE: _email_stepup_token_para(UID)}
    assert enforce_admin_mfa(_user("ADMIN", aal="aal1", mfa_email_enabled=True), request) is False


def test_precisa_step_up_considera_metodo_email_mesmo_sem_fator_gotrue():
    # O bug que motivou o plano: sem checar mfa_email_enabled, este usuário
    # (sem fatores no GoTrue) nunca seria mandado ao /mfa/verify.
    class _U:
        factors = []
        app_metadata = {"mfa_email_enabled": True}

    assert _precisa_step_up(_U()) is True


def test_precisa_step_up_falso_sem_nenhum_metodo():
    class _U:
        factors = []
        app_metadata = {}

    assert _precisa_step_up(_U()) is False


def test_login_com_so_email_habilitado_vai_para_step_up(monkeypatch):
    user = _FakeGoTrueUser(UID, fatores=[])
    user.app_metadata["mfa_email_enabled"] = True
    _fake_login(monkeypatch, user)
    with _client() as c:
        t = _csrf(c)
        r = c.post(
            "/login", data={"email": "a@b.com", "password": "x"},
            headers={"X-CSRF-Token": t}, follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/mfa/verify"


# ---------------------------------------------------------------------------
# Rotas de enrollment/verificação por e-mail
# ---------------------------------------------------------------------------

def test_enroll_email_envia_codigo_e_mostra_form(monkeypatch):
    vistos = {}

    async def fake_enviar(user_id, email):
        vistos["enviar"] = (user_id, email)

    monkeypatch.setattr(mfa_email, "enviar_codigo", fake_enviar)
    with _client() as c:
        t = _csrf(c)
        r = c.post("/mfa/enroll-email", headers={"X-CSRF-Token": t})
        assert r.status_code == 200
        assert vistos["enviar"] == (UID, "admin@bond.com")
        assert "admin@bond.com" in r.text


def test_enroll_email_confirmar_ativa_e_marca_cookie_de_sessao(monkeypatch):
    vistos = {}

    async def fake_verificar(user_id, codigo):
        vistos["verificar"] = (user_id, codigo)
        return True

    async def fake_marcar(user_id, habilitado):
        vistos["marcar"] = (user_id, habilitado)

    monkeypatch.setattr(mfa_email, "verificar_codigo", fake_verificar)
    monkeypatch.setattr(mfa, "marcar_email_mfa_habilitado", fake_marcar)
    with _client() as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/enroll-email/confirmar",
            data={"codigo": "123 456"},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/mfa?ok=1"
        assert vistos["verificar"] == (UID, "123456")
        assert vistos["marcar"] == (UID, True)
        assert mfa_email_stepup.EMAIL_STEPUP_COOKIE in c.cookies


def test_enroll_email_confirmar_com_codigo_invalido_nao_ativa(monkeypatch):
    async def fake_verificar(user_id, codigo):
        raise mfa_email.MfaErro("código inválido")

    marcou = []

    async def fake_marcar(user_id, habilitado):
        marcou.append(user_id)

    monkeypatch.setattr(mfa_email, "verificar_codigo", fake_verificar)
    monkeypatch.setattr(mfa, "marcar_email_mfa_habilitado", fake_marcar)
    with _client() as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/enroll-email/confirmar",
            data={"codigo": "000000"},
            headers={"X-CSRF-Token": t},
            follow_redirects=False,
        )
        assert r.status_code == 400
        assert "Código inválido" in r.text
        assert marcou == []


def test_verify_email_enviar_mostra_form_de_codigo(monkeypatch):
    async def fake_enviar(user_id, email):
        return None

    async def fake_fator(tokens):
        return None  # sem TOTP — só e-mail ativo

    monkeypatch.setattr(mfa_email, "enviar_codigo", fake_enviar)
    monkeypatch.setattr(mfa, "fator_verificado_id", fake_fator)
    with _client(_user("ADMIN", aal="aal1", mfa_email_enabled=True)) as c:
        t = _csrf(c)
        r = c.post("/mfa/verify/email/enviar", headers={"X-CSRF-Token": t})
        assert r.status_code == 200
        assert "admin@bond.com" in r.text


def test_verify_email_com_codigo_valido_marca_cookie_e_vai_para_home(monkeypatch):
    async def fake_verificar(user_id, codigo):
        return True

    monkeypatch.setattr(mfa_email, "verificar_codigo", fake_verificar)
    with _client(_user("ADMIN", aal="aal1", mfa_email_enabled=True)) as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/verify/email", data={"codigo": "123456"},
            headers={"X-CSRF-Token": t}, follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/workspace"
        assert mfa_email_stepup.EMAIL_STEPUP_COOKIE in c.cookies


def test_verify_email_com_codigo_invalido_reexibe_o_form(monkeypatch):
    async def fake_verificar(user_id, codigo):
        raise mfa_email.MfaErro("código inválido")

    async def fake_fator(tokens):
        return None  # sem TOTP — só e-mail ativo

    monkeypatch.setattr(mfa_email, "verificar_codigo", fake_verificar)
    monkeypatch.setattr(mfa, "fator_verificado_id", fake_fator)
    with _client(_user("ADMIN", aal="aal1", mfa_email_enabled=True)) as c:
        t = _csrf(c)
        r = c.post(
            "/mfa/verify/email", data={"codigo": "000000"},
            headers={"X-CSRF-Token": t}, follow_redirects=False,
        )
        assert r.status_code == 400
        assert "Código inválido" in r.text


# ---------------------------------------------------------------------------
# Reset por TI limpa os dois métodos (não só TOTP)
# ---------------------------------------------------------------------------

def test_resetar_mfa_limpa_metodo_email_tambem(monkeypatch):
    vistos = {}

    class _FakeAdminClient:
        class auth:
            class admin:
                @staticmethod
                async def update_user_by_id(user_id, patch):
                    vistos.setdefault("app_metadata_patches", []).append((user_id, patch))

                class mfa:
                    @staticmethod
                    async def delete_factor(params):
                        vistos.setdefault("delete_factor", []).append(params)

    async def fake_ensure_admin_client():
        return _FakeAdminClient()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_admin_connection():
        class _Conn:
            async def fetch(self, query, *args):
                return []  # sem fatores TOTP pendentes

        yield _Conn()

    async def fake_limpar_pendentes(user_id):
        vistos["limpar_pendentes"] = user_id

    monkeypatch.setattr(mfa, "ensure_admin_client", fake_ensure_admin_client)
    monkeypatch.setattr("app.db.admin_connection", fake_admin_connection)
    monkeypatch.setattr(mfa_email, "limpar_pendentes", fake_limpar_pendentes)

    import asyncio

    asyncio.run(mfa.resetar_mfa(UID))

    assert vistos["limpar_pendentes"] == UID
    # Duas gravações de app_metadata: mfa_enabled=False e mfa_email_enabled=False.
    patches: dict = {}
    for _, patch in vistos["app_metadata_patches"]:
        patches.update(patch.get("app_metadata", {}))
    assert patches.get("mfa_enabled") is False
    assert patches.get("mfa_email_enabled") is False

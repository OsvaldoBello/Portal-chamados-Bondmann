"""Fail-fast de segredos default em produção (Sprint 0 / item 0.2)."""

import pytest
from pydantic import ValidationError

from app.config import _DEFAULT_CSRF_SECRET, _DEFAULT_SESSION_SECRET, Settings

# Passa os dois segredos explicitamente em todo teste: o `.env` local do dev
# (nunca commitado) pode ter valores reais, que teriam precedência sobre o
# default da classe e mascarariam o comportamento sob teste.


def test_producao_recusa_boot_com_session_secret_default():
    with pytest.raises(ValidationError, match="SESSION_SECRET"):
        Settings(
            environment="production",
            session_secret=_DEFAULT_SESSION_SECRET,
            csrf_secret="algo-real-gerado-com-openssl",
        )


def test_producao_recusa_boot_com_csrf_secret_default():
    with pytest.raises(ValidationError, match="CSRF_SECRET"):
        Settings(
            environment="production",
            session_secret="algo-real-gerado-com-openssl",
            csrf_secret=_DEFAULT_CSRF_SECRET,
        )


def test_producao_recusa_boot_com_ambos_defaults():
    with pytest.raises(ValidationError, match="SESSION_SECRET.*CSRF_SECRET|CSRF_SECRET.*SESSION_SECRET"):
        Settings(
            environment="production",
            session_secret=_DEFAULT_SESSION_SECRET,
            csrf_secret=_DEFAULT_CSRF_SECRET,
        )


def test_producao_sobe_normal_com_segredos_reais():
    settings = Settings(
        environment="production",
        session_secret="segredo-real-de-producao",
        csrf_secret="outro-segredo-real-de-producao",
    )
    assert settings.is_production


def test_dev_sobe_normal_com_defaults():
    settings = Settings(environment="development")
    assert not settings.is_production


# ============================================================
# Flags da frente de IA de triagem (F0 — plano_md_mestre_IA.md, Seção 2.4).
# Defaults DESLIGADOS: deploy da F0 não muda comportamento algum sem env.
# ============================================================

_ENVS_IA = (
    "IA_TRIAGEM_ATIVA",
    "IA_TRIAGEM_DEPARTAMENTOS",
    "IA_TRIAGEM_MODO_SOMBRA",
    "IA_TRIAGEM_MODEL",
    "IA_TRIAGEM_MODEL_PASSE_B",
    "IA_TRIAGEM_BASE_URL",
    "IA_TRIAGEM_API_KEY",
    "IA_TRIAGEM_TIMEOUT_S",
    "IA_TRIAGEM_MAX_RODADAS",
    "IA_WORKER_DATABASE_URL",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GROQ_BASE_URL",
)


def _settings_ia(monkeypatch: pytest.MonkeyPatch, **overrides) -> Settings:
    """Settings sem interferência do `.env` local nem de env vars de IA do dev."""
    for env in _ENVS_IA:
        monkeypatch.delenv(env, raising=False)
    return Settings(_env_file=None, **overrides)


def test_ia_triagem_defaults_desligados(monkeypatch: pytest.MonkeyPatch):
    s = _settings_ia(monkeypatch)
    assert s.ia_triagem_ativa is False  # kill switch geral: nasce desligado
    assert s.ia_triagem_departamentos == "" and s.ia_triagem_departamentos_lista == []
    assert s.ia_triagem_modo_sombra is True  # sombra é o default seguro
    assert s.ia_triagem_model == "gpt-5.4-mini"
    assert s.ia_triagem_model_passe_b == ""
    assert s.ia_triagem_base_url == "https://api.openai.com/v1"
    assert s.ia_triagem_api_key == "" and s.ia_resumo_ativo is False
    assert s.ia_triagem_timeout_s == 30.0  # C6
    assert s.ia_triagem_max_rodadas == 2
    assert s.ia_worker_database_url == ""


def test_ia_triagem_aceita_alias_groq_na_transicao(monkeypatch: pytest.MonkeyPatch):
    """C5: rename groq_* → ia_triagem_* com fallback — o env antigo do Railway
    continua funcionando até o gestor migrar os nomes."""
    for env in _ENVS_IA:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "chave-legada")
    monkeypatch.setenv("GROQ_MODEL", "modelo-legado")
    monkeypatch.setenv("GROQ_BASE_URL", "https://provedor-legado.test/v1")
    s = Settings(_env_file=None)
    assert s.ia_triagem_api_key == "chave-legada" and s.ia_resumo_ativo is True
    assert s.ia_triagem_model == "modelo-legado"
    assert s.ia_triagem_base_url == "https://provedor-legado.test/v1"


def test_ia_triagem_nome_novo_prevalece_sobre_alias(monkeypatch: pytest.MonkeyPatch):
    for env in _ENVS_IA:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "chave-legada")
    monkeypatch.setenv("IA_TRIAGEM_API_KEY", "chave-nova")
    s = Settings(_env_file=None)
    assert s.ia_triagem_api_key == "chave-nova"


def test_ia_triagem_departamentos_lista_parseia_csv(monkeypatch: pytest.MonkeyPatch):
    s = _settings_ia(monkeypatch, ia_triagem_departamentos=" TI , Dpto Químico ,")
    assert s.ia_triagem_departamentos_lista == ["TI", "Dpto Químico"]

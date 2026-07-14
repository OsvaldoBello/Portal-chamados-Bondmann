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

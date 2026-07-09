"""Testes de `current_access_token` (Seção 3.4) — puro, sem banco/Storage.

Regressão: uploads (avatar/anexo) e `/realtime/config` chamavam
`request.cookies.get(ACCESS_COOKIE)` diretamente. Quando `get_current_user`
renova a sessão dentro da MESMA requisição (access token expirado + refresh
válido), o cookie ainda reflete o token velho — só é atualizado no Set-Cookie
da resposta (`SessionRefreshMiddleware`). Isso mandava um JWT expirado ao
Storage e disparava "Falha ao enviar a foto. Tente novamente." mesmo com a
sessão válida.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.auth.session import SessionTokens, current_access_token


def _request(*, cookie_token: str | None, refreshed: SessionTokens | None = None):
    return SimpleNamespace(
        state=SimpleNamespace(refreshed_session=refreshed),
        cookies={"sb_access": cookie_token} if cookie_token else {},
    )


def test_usa_cookie_quando_nao_houve_refresh_na_requisicao():
    request = _request(cookie_token="jwt-do-cookie")
    assert current_access_token(request) == "jwt-do-cookie"


def test_prefere_token_renovado_ao_cookie_velho():
    novo = SessionTokens(access_token="jwt-renovado", refresh_token="refresh-novo")
    request = _request(cookie_token="jwt-velho-expirado", refreshed=novo)
    assert current_access_token(request) == "jwt-renovado"


def test_sem_cookie_e_sem_refresh_retorna_none():
    request = SimpleNamespace(state=SimpleNamespace(), cookies={})
    assert current_access_token(request) is None

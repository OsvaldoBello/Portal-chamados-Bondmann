"""Chave do rate limit por IP (Sprint 1 / item 1.1, A5): o IP confiável é o
último da cadeia X-Forwarded-For (acrescentado pelo proxy do Railway), nunca
o primeiro (escrito pelo cliente e portanto forjável)."""

from starlette.requests import Request

from app.ratelimit import client_ip


def _request(headers: dict[str, str], client_host: str = "10.0.0.1") -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": raw_headers,
        "client": (client_host, 12345),
    }
    return Request(scope)


def test_ip_forjado_no_inicio_da_cadeia_nao_e_usado():
    """Um atacante que envia X-Forwarded-For com um IP falso na frente não
    consegue trocar a chave do rate limit: só o último IP (o que o proxy
    confiável adicionou) é usado."""
    req = _request({"X-Forwarded-For": "1.2.3.4, 203.0.113.9"})
    assert client_ip(req) == "203.0.113.9"


def test_ip_unico_no_header_e_usado():
    req = _request({"X-Forwarded-For": "203.0.113.9"})
    assert client_ip(req) == "203.0.113.9"


def test_sem_header_cai_no_ip_da_conexao_direta():
    req = _request({}, client_host="192.168.1.5")
    assert client_ip(req) == "192.168.1.5"


def test_header_com_espacos_e_normalizado():
    req = _request({"X-Forwarded-For": "1.2.3.4 ,  203.0.113.9  "})
    assert client_ip(req) == "203.0.113.9"

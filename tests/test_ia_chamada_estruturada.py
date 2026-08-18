"""Helper compartilhado de chamada com saída estruturada.

Extraído de `triagem._chamar_modelo` para ser reaproveitado pelo intake do
WhatsApp — estes testes travam o contrato (retry em JSON inválido, soma de
tokens entre tentativas, erro de provedor sem retry).
"""

from unittest.mock import AsyncMock, patch

import httpx
from pydantic import BaseModel

from app.ia.chamada_estruturada import chamar_modelo_estruturado
from app.ia.cliente import RespostaModelo


class _Saida(BaseModel):
    ok: bool


_MENSAGENS = [{"role": "user", "content": "oi"}]


async def _chamar(completar: AsyncMock):
    with patch("app.ia.chamada_estruturada.cliente.completar_chat", completar):
        return await chamar_modelo_estruturado(
            _MENSAGENS,
            model="modelo-x",
            api_key="chave",
            base_url="https://exemplo/v1",
            timeout_s=5.0,
            max_tokens=100,
            schema=_Saida,
        )


async def test_json_valido_na_primeira_tentativa():
    completar = AsyncMock(
        return_value=RespostaModelo(conteudo='{"ok": true}', tokens_entrada=10, tokens_saida=5)
    )
    saida, erro, tokens_in, tokens_out = await _chamar(completar)

    assert saida == _Saida(ok=True)
    assert erro is None
    assert (tokens_in, tokens_out) == (10, 5)
    assert completar.await_count == 1


async def test_json_invalido_faz_um_retry_e_soma_tokens():
    """Tokens da tentativa perdida contam: é custo real cobrado pelo provedor."""
    completar = AsyncMock(
        side_effect=[
            RespostaModelo(conteudo="isso não é json", tokens_entrada=10, tokens_saida=5),
            RespostaModelo(conteudo='{"ok": false}', tokens_entrada=8, tokens_saida=3),
        ]
    )
    saida, erro, tokens_in, tokens_out = await _chamar(completar)

    assert saida == _Saida(ok=False)
    assert erro is None
    assert (tokens_in, tokens_out) == (18, 8)
    assert completar.await_count == 2


async def test_json_invalido_nas_duas_tentativas_devolve_erro():
    completar = AsyncMock(
        return_value=RespostaModelo(conteudo="{}", tokens_entrada=4, tokens_saida=2)
    )
    saida, erro, tokens_in, tokens_out = await _chamar(completar)

    assert saida is None
    assert erro is not None and erro.startswith("json_invalido")
    assert (tokens_in, tokens_out) == (8, 4)
    assert completar.await_count == 2


async def test_falha_de_provedor_nao_tem_retry():
    """Erro de rede vira erro silencioso direto — o chamador nunca é bloqueado."""
    completar = AsyncMock(side_effect=httpx.ConnectError("sem rede"))
    saida, erro, _tokens_in, _tokens_out = await _chamar(completar)

    assert saida is None
    assert erro is not None and erro.startswith("provedor:")
    assert completar.await_count == 1

"""Chamada de IA com saída estruturada + retry — extraído de
``app/ia/triagem.py::_chamar_modelo`` (Seção 2.1) para ser reaproveitado por
outros fluxos de IA (ex.: intake de chamado via WhatsApp) sem duplicar a
lógica de retry/validação. Comportamento idêntico ao original; ``triagem.py``
passou a ser um wrapper fino desta função.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from app.ia import cliente


def _soma_tokens(acumulado: int | None, novo: int | None) -> int | None:
    if novo is None:
        return acumulado
    return (acumulado or 0) + novo


async def chamar_modelo_estruturado[SaidaT: BaseModel](
    mensagens: list[dict[str, Any]],
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout_s: float,
    max_tokens: int,
    schema: type[SaidaT],
) -> tuple[SaidaT | None, str | None, int | None, int | None]:
    """Uma chamada estruturada + 1 retry em JSON inválido.

    Devolve ``(saida, erro, tokens_entrada, tokens_saida)`` — tokens somados
    entre tentativas (custo real). Falha de provedor não tem retry: vira erro
    silencioso direto (o chamador nunca deve segurar seu fluxo por causa disso)."""
    tokens_in: int | None = None
    tokens_out: int | None = None
    erro: str | None = None
    for _tentativa in (1, 2):
        try:
            resposta = await cliente.completar_chat(
                mensagens=mensagens,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout_s=timeout_s,
                max_tokens=max_tokens,
                json_mode=True,
            )
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            return None, f"provedor: {exc}", tokens_in, tokens_out
        tokens_in = _soma_tokens(tokens_in, resposta.tokens_entrada)
        tokens_out = _soma_tokens(tokens_out, resposta.tokens_saida)
        try:
            return schema.model_validate_json(resposta.conteudo), None, tokens_in, tokens_out
        except ValidationError as exc:
            erro = f"json_invalido: {exc.error_count()} erro(s) de schema"
            continue
    return None, erro, tokens_in, tokens_out

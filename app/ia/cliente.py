"""Cliente de modelo compatível-OpenAI (``/chat/completions``) — F0, C2.

Extraído de ``app/services/ia_resumo.py`` para ser o ponto único de chamada
HTTP a provedores de IA (resumo do Químico hoje; motor de triagem na F1+).
Provedor-agnóstico por construção: recebe ``base_url``/``api_key``/``model``
como parâmetros — trocar de provedor é só env (C5), nunca código.

Contrato:
- **Não lê settings nem engole erro**: quem chama decide a política de falha
  (o padrão do projeto é falha silenciosa — Regra de Ouro #5 do plano IA).
- Devolve :class:`RespostaModelo` com o texto e a contagem de tokens reportada
  pelo provedor (``usage``) — insumo do custo auditável em ``ia_triagens``
  (Regra de Ouro #9), gravado a partir da F1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

# Tabela de preços US$/M tokens (entrada, saída) — atualizada junto com trocas
# de modelo (Seção 6 do plano IA). Alimenta `ia_triagens.custo_usd` (custo
# auditável, Regra de Ouro #9). Modelo fora da tabela ⇒ custo None (não inventa).
PRECOS_USD_POR_MTOK: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.75, 4.50),
}


def custo_usd(model: str, tokens_entrada: int | None, tokens_saida: int | None) -> float | None:
    """Custo estimado da chamada, ou ``None`` se faltar preço/contagem."""
    precos = PRECOS_USD_POR_MTOK.get(model)
    if precos is None or tokens_entrada is None or tokens_saida is None:
        return None
    entrada, saida = precos
    return (tokens_entrada * entrada + tokens_saida * saida) / 1_000_000


@dataclass(frozen=True)
class RespostaModelo:
    """Saída de uma chamada de chat: texto + metadados de uso."""

    conteudo: str
    tokens_entrada: int | None = None
    tokens_saida: int | None = None


async def completar_chat(
    *,
    mensagens: list[dict[str, Any]],
    model: str,
    api_key: str,
    base_url: str,
    timeout_s: float,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    json_mode: bool = False,
) -> RespostaModelo:
    """Uma chamada de chat com resposta em texto (sem loop de agente).

    ``json_mode=True`` pede saída JSON ao provedor (``response_format`` do
    formato OpenAI) — a validação de schema continua sendo do chamador.

    Levanta ``httpx.HTTPError`` (rede/timeout/status) ou ``KeyError``/
    ``IndexError``/``ValueError`` (payload inesperado) — o chamador trata.
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    payload: dict[str, Any] = {
        "model": model,
        "messages": mensagens,
        "temperature": temperature,
    }
    if max_tokens is not None:
        # Nome moderno do parâmetro no formato OpenAI: a família GPT-5 rejeita
        # o legado `max_tokens` (400 unsupported_parameter — visto em produção
        # com gpt-5.4-mini, 2026-07-22); `max_completion_tokens` é aceito
        # também pelos demais provedores compatíveis (ex.: Groq).
        payload["max_completion_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    conteudo = (data["choices"][0]["message"]["content"] or "").strip()
    usage = data.get("usage") or {}
    return RespostaModelo(
        conteudo=conteudo,
        tokens_entrada=usage.get("prompt_tokens"),
        tokens_saida=usage.get("completion_tokens"),
    )

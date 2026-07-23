"""Resumo de chamado por IA → nota interna (feature Químico, 2026-07).

Na abertura de um chamado do Químico, uma IA lê o assunto, a descrição e os
campos do formulário dinâmico e devolve um **resumo curto para a equipe
técnica**, gravado em ``chamados.resumo_ia`` (migration 0049) e exibido como
nota interna — nunca ao cliente.

Decisões de arquitetura (ver plano da feature):
- **Chamada direta do backend** (não n8n), disparada como *background task* do
  FastAPI em ``criar_chamado`` — o mesmo mecanismo já usado para e-mail
  (:func:`app.notification.agendar_notificacao_email`). Nunca bloqueia o redirect
  da abertura.
- **Provedor plugável** via API compatível com o formato OpenAI
  (``/chat/completions``), pela camada única :mod:`app.ia.cliente` (F0/C2 da
  frente de IA). Provedor atual: OpenAI / gpt-5.4-mini (decisão C5). Config em
  :class:`app.config.Settings` (``ia_triagem_*``).
- **Escrita de sistema**: a task roda fora do request do autor (o autor não pode
  escrever ``resumo_ia``). Grava via :func:`app.db.admin_connection` — a conexão
  administrativa interna já existente, sem claims de usuário.
- **Tolerante a falha**: qualquer erro (sem chave, timeout, HTTP, resposta vazia)
  é logado e engolido; o chamado permanece válido, só sem resumo.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.db import admin_connection
from app.domain.formularios_quimico import rotular
from app.ia import cliente

log = logging.getLogger("app.ia_resumo")

# Timeout: herdado de settings.ia_triagem_timeout_s (30 s unificado — C6).
_MAX_TOKENS = 400

_SYSTEM_PROMPT = (
    "Você é um assistente do departamento químico de uma indústria. Resuma o "
    "chamado abaixo para a equipe técnica interna em português do Brasil. Seja "
    "objetivo: 2 a 4 frases + até 4 bullets com os pontos críticos (produto/lote, "
    "local, prazo, riscos). Destaque o que exige atenção imediata. Não invente "
    "dados que não estejam no chamado. Não repita rótulos vazios."
)


def _montar_conteudo(
    titulo: str, descricao: str, categoria: str | None, dados_formulario: dict[str, Any]
) -> str:
    """Texto do chamado enviado à IA, com os campos do formulário rotulados."""
    linhas = [f"Categoria: {categoria or '—'}", f"Assunto: {titulo}", "", "Descrição:", descricao]
    pares = rotular(categoria, dados_formulario)
    if pares:
        linhas.append("")
        linhas.append("Campos do formulário:")
        linhas.extend(f"- {label}: {valor}" for label, valor in pares)
    return "\n".join(linhas)


async def resumir_chamado(
    titulo: str,
    descricao: str,
    categoria: str | None,
    dados_formulario: dict[str, Any],
) -> str | None:
    """Chama a IA e devolve o resumo, ou ``None`` se desligada/indisponível.

    Nunca levanta: qualquer erro vira ``None`` (o chamado não depende disto)."""
    settings = get_settings()
    if not settings.ia_resumo_ativo:
        log.info("[IA RESUMO] Desligado (sem IA_TRIAGEM_API_KEY). Chamado sem resumo.")
        return None

    mensagens = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _montar_conteudo(titulo, descricao, categoria, dados_formulario),
        },
    ]
    try:
        resposta = await cliente.completar_chat(
            mensagens=mensagens,
            model=settings.ia_triagem_model,
            api_key=settings.ia_triagem_api_key,
            base_url=settings.ia_triagem_base_url,
            timeout_s=settings.ia_triagem_timeout_s,
            temperature=0.2,
            max_tokens=_MAX_TOKENS,
        )
        return resposta.conteudo or None
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.warning("[IA RESUMO] Falha ao gerar resumo: %s", exc)
        return None


async def _salvar_resumo(chamado_id: str, texto: str) -> None:
    """Grava o resumo no chamado via conexão administrativa (sem claims)."""
    async with admin_connection() as conn:
        await conn.execute(
            "UPDATE chamados SET resumo_ia = $2, resumo_ia_em = now() WHERE id = $1::uuid",
            chamado_id,
            texto,
        )


async def gerar_e_salvar_resumo(
    chamado_id: str,
    titulo: str,
    descricao: str,
    categoria: str | None,
    dados_formulario: dict[str, Any],
) -> None:
    """Entrada da background task: gera o resumo e persiste se houver texto.

    Tolerante a falha ponta a ponta — usado com ``BackgroundTasks.add_task``."""
    try:
        texto = await resumir_chamado(titulo, descricao, categoria, dados_formulario)
        if texto:
            await _salvar_resumo(chamado_id, texto)
            log.info("[IA RESUMO] Resumo gravado no chamado %s.", chamado_id)
    except Exception as exc:  # noqa: BLE001 — task de fundo nunca deve derrubar nada
        log.warning("[IA RESUMO] Erro inesperado no chamado %s: %s", chamado_id, exc)

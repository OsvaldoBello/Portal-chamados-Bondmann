"""Formatação do catálogo de categorias/subcategorias para prompt de IA —
extraído de ``app/ia/triagem.py`` para ser reaproveitado por outros fluxos
(ex.: intake de chamado via WhatsApp) sem duplicar a lógica."""

from __future__ import annotations

from typing import Any


def nome_categoria(item: Any) -> str:
    """Nome de uma entrada do catálogo — aceita ``str`` (formato histórico) ou
    ``dict`` com ``nome``/``subcategorias`` (formato da F8)."""
    return str(item.get("nome") or "") if isinstance(item, dict) else str(item)


def linha_catalogo(item: Any) -> str:
    """Uma categoria do catálogo, com as subcategorias ativas quando houver —
    o modelo só pode escolher destino que apareça aqui."""
    nome = nome_categoria(item)
    subs = [
        str(s.get("nome") or "") if isinstance(s, dict) else str(s)
        for s in (item.get("subcategorias") or [] if isinstance(item, dict) else [])
    ]
    subs = [s for s in subs if s]
    return f"- {nome}" + (f" → subcategorias: {'; '.join(subs)}" if subs else "")

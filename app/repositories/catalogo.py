"""Repositório de catálogos — categorias/subcategorias/departamentos (Seção 3.1/5.1).

Extraído de `ChamadosRepo` (Sprint 2 / item 2.1, M1). Mesma regra das demais
partes do domínio: cada método abre uma transação curta via
:func:`rls_connection`, RLS impõe autorização/isolamento.
"""

from __future__ import annotations

from typing import Any

from app import cache
from app.db import rls_connection

# Chaves e TTL do cache de catálogos globais (Seção 2.3). Invalidados na escrita
# pelas rotas de admin (ver app/routes/admin.py).
CACHE_CATEGORIAS = "categorias_ativas"
CACHE_DEPARTAMENTOS = "departamentos_ativos"
CACHE_SUBCATEGORIAS = "subcategorias_ativas"  # sufixado por categoria_id
CATALOGO_TTL = 90.0  # segundos


class CatalogoRepo:
    """Leitura de categorias/subcategorias/departamentos ativos (cacheado)."""

    async def categorias_ativas(
        self, claims: dict, departamento_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Categorias ativas, opcionalmente filtradas por departamento.

        Categorias pertencem a um departamento (migration 0019); a abertura mostra
        só as do setor de destino escolhido. Cacheado por departamento."""
        chave = f"{CACHE_CATEGORIAS}:{departamento_id or 'all'}"
        cached = cache.get(chave)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT id, nome FROM categorias
                    WHERE ativo = true
                      AND ($1::uuid IS NULL OR departamento_id = $1::uuid)
                    ORDER BY nome""",
                departamento_id,
            )
        resultado = [dict(r) for r in rows]
        cache.set(chave, resultado, CATALOGO_TTL)
        return resultado

    async def categoria_valida(
        self, claims: dict, *, categoria_id: str, departamento_id: str
    ) -> bool:
        """A categoria existe, está ativa e pertence ao departamento informado?
        Defesa em profundidade contra POST com par categoria/departamento forjado."""
        cats = await self.categorias_ativas(claims, departamento_id)
        return any(str(c["id"]) == str(categoria_id) for c in cats)

    async def departamentos_ativos(self, claims: dict) -> list[dict[str, Any]]:
        """Todos os setores ativos da empresa (catálogo unificado — 0027). Cada um
        traz ``recebe_chamados``: só os que têm fila de atendimento (TI/RH/Marketing,
        hoje) podem ser destino de chamado; os demais só identificam quem abriu."""
        cached = cache.get(CACHE_DEPARTAMENTOS)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT id, nome, recebe_chamados FROM departamentos WHERE ativo = true ORDER BY nome"
            )
        resultado = [dict(r) for r in rows]
        cache.set(CACHE_DEPARTAMENTOS, resultado, CATALOGO_TTL)
        return resultado

    async def departamentos_destino_ativos(self, claims: dict) -> list[dict[str, Any]]:
        """Subconjunto de :meth:`departamentos_ativos` que pode ser destino de
        chamado (``recebe_chamados``) — usado no dropdown "Departamento de destino"
        e no repasse de setor (Workspace)."""
        todos = await self.departamentos_ativos(claims)
        return [d for d in todos if d.get("recebe_chamados")]

    async def subcategorias_ativas(
        self, claims: dict, categoria_id: str
    ) -> list[dict[str, Any]]:
        """Subcategorias ativas de uma categoria (cascade da abertura de chamado).

        Cacheado por categoria (chave sufixada por ``categoria_id``). Retorna ``[]``
        para categoria inexistente/sem subcategorias — o form trata o vazio."""
        chave = f"{CACHE_SUBCATEGORIAS}:{categoria_id}"
        cached = cache.get(chave)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT id, nome FROM subcategorias
                    WHERE categoria_id = $1::uuid AND ativo = true
                    ORDER BY nome""",
                categoria_id,
            )
        resultado = [dict(r) for r in rows]
        cache.set(chave, resultado, CATALOGO_TTL)
        return resultado

    async def subcategoria_valida(
        self, claims: dict, *, categoria_id: str, subcategoria_id: str
    ) -> bool:
        """A subcategoria existe, está ativa e pertence à categoria informada?
        Defesa em profundidade contra POST com par categoria/subcategoria forjado."""
        subs = await self.subcategorias_ativas(claims, categoria_id)
        return any(str(s["id"]) == str(subcategoria_id) for s in subs)

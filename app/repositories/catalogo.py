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
CACHE_PUBLICO_USUARIO = "publico_alvo_usuario"  # sufixado por user id
CATALOGO_TTL = 90.0  # segundos


class CatalogoRepo:
    """Leitura de categorias/subcategorias/departamentos ativos (cacheado)."""

    async def _publico_usuario(self, claims: dict) -> str:
        """'PJ' se o solicitante é do departamento Representantes, senão 'CLT'
        (0076 — RH segmenta o catálogo por público; representante é a única
        população PJ hoje). Cacheado por usuário — o departamento de alguém
        raramente muda dentro da janela do TTL."""
        user_id = claims.get("sub")
        chave = f"{CACHE_PUBLICO_USUARIO}:{user_id}"
        cached = cache.get(chave)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            nome_dep = await conn.fetchval(
                """SELECT d.nome FROM perfis p
                     LEFT JOIN departamentos d ON d.id = p.departamento_id
                    WHERE p.id = $1::uuid""",
                user_id,
            )
        resultado = "PJ" if nome_dep == "Representantes" else "CLT"
        cache.set(chave, resultado, CATALOGO_TTL)
        return resultado

    async def categorias_ativas(
        self,
        claims: dict,
        departamento_id: str | None = None,
        *,
        filtrar_publico: bool = False,
    ) -> list[dict[str, Any]]:
        """Categorias ativas, opcionalmente filtradas por departamento.

        Categorias pertencem a um departamento (migration 0019); a abertura mostra
        só as do setor de destino escolhido. Cacheado por departamento.

        ``filtrar_publico=True`` (0076) restringe também pelo público-alvo
        (CLT/PJ/AMBOS) do PRÓPRIO solicitante — uso na abertura de chamado
        (quem preenche o form é quem vai usar a categoria). Staff reclassificando
        um chamado alheio (Workspace) mantém ``False``: precisa enxergar o
        catálogo inteiro do setor, não só o próprio público."""
        publico = await self._publico_usuario(claims) if filtrar_publico else None
        chave = f"{CACHE_CATEGORIAS}:{departamento_id or 'all'}:{publico or 'all'}"
        cached = cache.get(chave)
        if cached is not None:
            return cached
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """SELECT id, nome FROM categorias
                    WHERE ativo = true
                      AND ($1::uuid IS NULL OR departamento_id = $1::uuid)
                      AND ($2::text IS NULL OR publico_alvo IN ('AMBOS', $2::text))
                    ORDER BY nome""",
                departamento_id,
                publico,
            )
        resultado = [dict(r) for r in rows]
        cache.set(chave, resultado, CATALOGO_TTL)
        return resultado

    async def categoria_valida(
        self,
        claims: dict,
        *,
        categoria_id: str,
        departamento_id: str,
        filtrar_publico: bool = False,
    ) -> bool:
        """A categoria existe, está ativa e pertence ao departamento informado?
        Defesa em profundidade contra POST com par categoria/departamento forjado
        (e, com ``filtrar_publico=True``, contra POST forjando uma categoria de
        outro público)."""
        cats = await self.categorias_ativas(
            claims, departamento_id, filtrar_publico=filtrar_publico
        )
        return any(str(c["id"]) == str(categoria_id) for c in cats)

    async def nome_categoria(self, claims: dict, categoria_id: str) -> str | None:
        """Nome de uma categoria pelo id (ou ``None`` se não existir/for visível).

        Usado para resolver o layout dinâmico do Químico, cujo schema é indexado
        pelo nome da categoria (app/domain/formularios_quimico.py)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                "SELECT nome FROM categorias WHERE id = $1::uuid", categoria_id
            )
        return row["nome"] if row else None

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

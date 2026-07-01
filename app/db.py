"""Acesso a dados via asyncpg sob Supavisor (transaction mode).

Modelo de isolamento multi-tenant (Seção 3.1 / 2.1 do plano mestre):

- O pool conecta no Supavisor em **transaction mode (porta 6543)**, com
  ``statement_cache_size=0`` (prepared statements desligados são exigência do
  pooler em transaction mode).
- Para cada operação de domínio, abrimos **uma transação** e injetamos os
  claims do usuário com escopo transacional::

      SET LOCAL ROLE authenticated;
      SELECT set_config('request.jwt.claims', $claims, true);  -- true = LOCAL

  Assim as políticas RLS que leem ``auth.uid()`` / ``auth.jwt()`` funcionam
  **mesmo sob pooling**, porque ``SET LOCAL`` vive só na transação e não vaza
  para a próxima conexão emprestada do pool.

A ``service_role`` NUNCA é usada aqui para servir dados de usuário.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from app.config import Settings

_pool: asyncpg.Pool | None = None


async def init_pool(settings: Settings) -> asyncpg.Pool:
    """Cria o pool global. Chamado no lifespan da app."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            # Obrigatório sob Supavisor transaction mode (Seção 2.1).
            statement_cache_size=0,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool de banco não inicializado (init_pool não foi chamado).")
    return _pool


async def _ensure_pool() -> asyncpg.Pool:
    """Garante o pool mesmo sem lifespan (ex.: serverless/Vercel cold start).

    Idempotente e barato quando já inicializado. Levanta erro claro se não há
    ``DATABASE_URL`` configurada."""
    global _pool
    if _pool is None:
        from app.config import get_settings

        settings = get_settings()
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL ausente — configure o banco (Supavisor 6543).")
        await init_pool(settings)
    return _pool  # type: ignore[return-value]


@asynccontextmanager
async def rls_connection(claims: dict[str, Any]) -> AsyncIterator[asyncpg.Connection]:
    """Conexão transacional com claims do usuário aplicados para RLS.

    Use para TODA leitura/escrita de domínio em nome de um usuário autenticado.
    ``claims`` deve conter ao menos ``sub`` (lido por ``auth.uid()``) e ``role``.
    """
    pool = await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Papel de banco do PostgREST/Supabase para usuários autenticados.
            await conn.execute("SET LOCAL ROLE authenticated")
            # Claims em escopo LOCAL (transação) — compatível com transaction mode.
            await conn.execute(
                "SELECT set_config('request.jwt.claims', $1, true)",
                json.dumps(claims),
            )
            yield conn


@asynccontextmanager
async def admin_connection() -> AsyncIterator[asyncpg.Connection]:
    """Conexão sem claims de usuário, para tarefas administrativas internas.

    NÃO usar em rota acessível por request de usuário. Não faz downgrade de
    role; herda o papel da DSN. Uso restrito e auditado (Seção 3.1).
    """
    pool = await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            yield conn

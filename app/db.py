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
from contextvars import ContextVar
from typing import Any, AsyncIterator

import asyncpg

from app.config import Settings

_pool: asyncpg.Pool | None = None

async def _apply_rls_claims(conn: asyncpg.Connection, claims: dict[str, Any]) -> None:
    """Injeta papel + claims no escopo da transação (RLS) em **um** round-trip.

    ``SET LOCAL ROLE`` e ``set_config`` vão num único comando (simple query) para
    cortar uma ida ao banco por request — relevante contra banco remoto. O JSON
    de claims é embutido como literal com aspas simples duplicadas (à prova de
    injeção: ``standard_conforming_strings`` on; o único terminador de string é a
    aspa simples, que escapamos)."""
    payload = json.dumps(claims).replace("'", "''")
    await conn.execute(
        "SET LOCAL ROLE authenticated;"
        f" SELECT set_config('request.jwt.claims', '{payload}', true);"
    )


class _RLSHolder:
    """Conexão RLS **por request**, aberta preguiçosamente na 1ª query e reusada.

    ``rls_request_scope`` registra um holder no contextvar sem tocar no banco;
    só a primeira chamada a ``rls_connection`` abre a conexão/transação e aplica
    os claims uma única vez. Assim, requests que não acessam o domínio (ou testes
    com repositórios fake) **nunca** abrem conexão. Corta os round-trips de
    ``SET LOCAL ROLE`` + ``set_config`` por método (Seção 2.1)."""

    def __init__(self, claims: dict[str, Any]) -> None:
        self.claims = claims
        self.conn: asyncpg.Connection | None = None
        self._acquire_cm: Any = None
        self._tx: Any = None

    async def get_conn(self) -> asyncpg.Connection:
        if self.conn is None:
            pool = await _ensure_pool()
            self._acquire_cm = pool.acquire()
            conn = await self._acquire_cm.__aenter__()
            self._tx = conn.transaction()
            await self._tx.start()
            await _apply_rls_claims(conn, self.claims)
            self.conn = conn
        return self.conn

    async def close(self, exc: BaseException | None) -> None:
        if self.conn is None:
            return
        try:
            if exc is None:
                await self._tx.commit()
            else:
                await self._tx.rollback()
        except Exception:  # noqa: BLE001 — garante liberação mesmo se o commit falhar
            try:
                await self._tx.rollback()
            except Exception:  # noqa: BLE001
                pass
        finally:
            await self._acquire_cm.__aexit__(None, None, None)
            self.conn = None


_request_holder: ContextVar["_RLSHolder | None"] = ContextVar("_request_holder", default=None)


async def init_pool(settings: Settings) -> asyncpg.Pool:
    """Cria o pool global. Chamado no lifespan da app (ou sob demanda via `_ensure_pool`).

    Processo persistente (Railway — Sprint 1 / item 1.8, M10: alvo único de
    deploy, decisão 2026-07-15): usa os valores configurados diretamente, sem
    o modo restrito que a Vercel serverless exigia (funções efêmeras, pool
    forçado a `min_size=0`). Ver Seção 2.1 do plano mestre.
    """
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            # Obrigatório sob Supavisor transaction mode (Seção 2.1).
            statement_cache_size=0,
            command_timeout=30,
            max_inactive_connection_lifetime=300.0,
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


def pool_stats() -> dict[str, int] | None:
    """Ocupação do pool asyncpg (Sprint 2 / item 2.6) — ``None`` se ainda não
    inicializado (modo limitado, ver ``lifespan`` em app/main.py)."""
    if _pool is None:
        return None
    return {
        "size": _pool.get_size(),
        "idle": _pool.get_idle_size(),
        "min_size": _pool.get_min_size(),
        "max_size": _pool.get_max_size(),
    }


async def _ensure_pool() -> asyncpg.Pool:
    """Garante o pool mesmo se chamado antes do lifespan rodar.

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
    # Dentro de um request-scope: reusa a conexão do holder (abre na 1ª vez).
    holder = _request_holder.get()
    if holder is not None:
        conn = await holder.get_conn()
        yield conn
        return

    # Standalone (fora de request-scope, ex.: script/admin): tx própria.
    pool = await _ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await _apply_rls_claims(conn, claims)
            yield conn


@asynccontextmanager
async def rls_request_scope(claims: dict[str, Any]) -> AsyncIterator[None]:
    """Registra um :class:`_RLSHolder` no contextvar para todo o request.

    **Não toca no banco** aqui: a conexão é aberta preguiçosamente pela primeira
    ``rls_connection`` do request e reusada pelas demais (uma só aplicação de
    claims). Use como dependência ``yield`` do FastAPI nos contextos de rota."""
    holder = _RLSHolder(claims)
    token = _request_holder.set(holder)
    exc: BaseException | None = None
    try:
        yield
    except BaseException as e:  # noqa: BLE001 — propaga após fechar a transação
        exc = e
        raise
    finally:
        _request_holder.reset(token)
        await holder.close(exc)


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

"""Cliente Supabase assíncrono — usado APENAS para Auth (Seção 1.4).

Login/refresh/signup/logout passam pelo GoTrue via supabase-py async com a
anon key. Dados de domínio NÃO passam por aqui (vão por asyncpg, ver db.py).
"""

from __future__ import annotations

from supabase import AsyncClient, acreate_client

from app.config import Settings

_client: AsyncClient | None = None


async def init_supabase(settings: Settings) -> AsyncClient:
    global _client
    if _client is None:
        _client = await acreate_client(settings.supabase_url, settings.supabase_anon_key)
    return _client


def get_supabase() -> AsyncClient:
    if _client is None:
        raise RuntimeError("Cliente Supabase não inicializado.")
    return _client

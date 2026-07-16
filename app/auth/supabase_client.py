"""Cliente Supabase assíncrono — usado APENAS para Auth (Seção 1.4).

Login/refresh/signup/logout passam pelo GoTrue via supabase-py async com a
anon key. Dados de domínio NÃO passam por aqui (vão por asyncpg, ver db.py).
"""

from __future__ import annotations

from app.config import Settings
from supabase import AsyncClient, acreate_client

_client: AsyncClient | None = None
_admin_client: AsyncClient | None = None


async def init_supabase(settings: Settings) -> AsyncClient:
    global _client
    if _client is None:
        _client = await acreate_client(settings.supabase_url, settings.supabase_anon_key)
    return _client


def get_supabase() -> AsyncClient:
    if _client is None:
        raise RuntimeError("Cliente Supabase não inicializado.")
    return _client


async def ensure_supabase() -> AsyncClient:
    """Garante o cliente mesmo sem lifespan (serverless). Idempotente."""
    if _client is None:
        from app.config import get_settings

        settings = get_settings()
        if not (settings.supabase_url and settings.supabase_anon_key):
            raise RuntimeError("Supabase não configurado (SUPABASE_URL/ANON_KEY).")
        await init_supabase(settings)
    return get_supabase()


async def ensure_admin_client() -> AsyncClient | None:
    """Cliente Supabase com a **service_role key** para tarefas administrativas
    (GoTrue Admin API: criar/promover usuários). NUNCA vai ao browser — usado só
    server-side em rotas restritas ao TI (Seção 3.1 / 6.2).

    Retorna ``None`` quando a service_role não está configurada (ex.: dev), para
    a rota degradar com uma mensagem em vez de quebrar. Idempotente/cacheado.
    """
    global _admin_client
    if _admin_client is None:
        from app.config import get_settings

        settings = get_settings()
        if not (settings.supabase_url and settings.supabase_service_role_key):
            return None
        _admin_client = await acreate_client(
            settings.supabase_url, settings.supabase_service_role_key
        )
    return _admin_client


async def create_isolated_client() -> AsyncClient:
    """Cria um cliente Supabase **novo e isolado** (sessão própria).

    Usado no fluxo de redefinição de senha (verify_otp → update_user): esses
    passos mutam a sessão do GoTrue, e reusar o cliente global compartilhado
    causaria corrida entre requests concorrentes. Como redefinição é rara, o
    custo de um cliente efêmero é aceitável.
    """
    from app.config import get_settings

    settings = get_settings()
    if not (settings.supabase_url and settings.supabase_anon_key):
        raise RuntimeError("Supabase não configurado (SUPABASE_URL/ANON_KEY).")
    return await acreate_client(settings.supabase_url, settings.supabase_anon_key)

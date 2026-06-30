"""Configuração e segredos (Pydantic Settings, Seções 3.6 / 6.2).

Toda configuração vem de variáveis de ambiente. `.env` nunca é commitado;
em produção os segredos vêm do ambiente do Railway. Nenhuma chave é
hard-coded — ver REGRA DURA da Seção 6.2 do plano mestre.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Ambiente ---
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # --- Supabase ---
    supabase_url: str = Field(default="")
    supabase_anon_key: str = Field(default="")
    # Restrita a tarefas administrativas/auditadas; NUNCA enviada ao browser.
    supabase_service_role_key: str = Field(default="")
    # Preenchido apenas se o projeto usar HS256 legado (Seção 3.6).
    supabase_jwt_secret: str = Field(default="")

    # --- Banco (asyncpg via Supavisor, transaction mode) ---
    database_url: str = Field(default="")
    db_pool_min_size: int = Field(default=2)
    db_pool_max_size: int = Field(default=10)

    # --- Segredos de aplicação ---
    session_secret: str = Field(default="dev-insecure-session-secret-change-me")
    csrf_secret: str = Field(default="dev-insecure-csrf-secret-change-me")

    # --- Cookies ---
    cookie_secure: bool = Field(default=True)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def jwks_url(self) -> str:
        """Endpoint JWKS do GoTrue para verificação assimétrica (Seção 3.6)."""
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def supabase_ws_url(self) -> str:
        """Origem wss do Realtime, para o connect-src da CSP (Seção 3.8)."""
        return self.supabase_url.replace("https://", "wss://").replace("http://", "ws://")


@lru_cache
def get_settings() -> Settings:
    """Settings em singleton (cacheado por processo)."""
    return Settings()

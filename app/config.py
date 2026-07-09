"""Configuração e segredos (Pydantic Settings, Seções 3.6 / 6.2).

Toda configuração vem de variáveis de ambiente. `.env` nunca é commitado;
em produção os segredos vêm do ambiente do Railway. Nenhuma chave é
hard-coded — ver REGRA DURA da Seção 6.2 do plano mestre.
"""

from __future__ import annotations

import os
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

    # --- Storage de anexos (Seção 3.9) ---
    anexos_bucket: str = Field(default="chamados-anexos")
    # Limite de 10MB por arquivo (rejeição server-side antes de persistir).
    anexo_max_bytes: int = Field(default=10 * 1024 * 1024)
    # TTL da signed URL: 1 hora (C2). Regenerada a cada renderização; nunca cacheada.
    signed_url_ttl: int = Field(default=3600)

    # --- Storage de avatares (Fase 7 — 2026-07-09) ---
    # `[DECISÃO DE ENGENHARIA]` bucket PÚBLICO (diferente dos anexos): avatar não
    # é dado sensível e é renderizado em muitas células de card ao mesmo tempo
    # (fila/kanban) — URL direta e estável, sem custo de assinatura por render.
    avatares_bucket: str = Field(default="avatares")
    avatar_max_bytes: int = Field(default=2 * 1024 * 1024)  # 2MB — só imagem

    # --- Segredos de aplicação ---
    session_secret: str = Field(default="dev-insecure-session-secret-change-me")
    csrf_secret: str = Field(default="dev-insecure-csrf-secret-change-me")

    # --- Cookies ---
    cookie_secure: bool = Field(default=True)

    # --- SMTP / E-mail (Notificações — fallback quando Mailgun não está ativo) ---
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=587)
    smtp_user: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from: str = Field(default="no-reply@bondmann.com.br")
    site_url: str = Field(default="http://localhost:8000")
    inbound_email_domain: str = Field(default="")
    inbound_email_secret: str = Field(default="")

    # --- Mailgun (provedor transacional preferencial: envio via API HTTP +
    #     recebimento de respostas via webhook inbound). Usar API é mais confiável
    #     que SMTP em ambiente serverless (Vercel): sem socket TCP bloqueante,
    #     sem timeouts de STARTTLS, entrega por HTTPS. ---
    mailgun_api_key: str = Field(default="")
    mailgun_domain: str = Field(default="")
    # Região da conta: US = https://api.mailgun.net | EU = https://api.eu.mailgun.net
    mailgun_base_url: str = Field(default="https://api.mailgun.net")

    @property
    def email_from(self) -> str:
        """Remetente do e-mail. Para alinhamento DKIM/DMARC no Mailgun o domínio
        do From deve casar com ``mailgun_domain`` (domínio verificado). Se
        ``smtp_from`` foi definido, respeita-o; senão deriva do domínio Mailgun.
        """
        if self.smtp_from:
            return self.smtp_from
        if self.mailgun_domain:
            return f"Portal Bondmann <no-reply@{self.mailgun_domain}>"
        return "no-reply@bondmann.com.br"

    @property
    def mailgun_ativo(self) -> bool:
        return bool(self.mailgun_api_key and self.mailgun_domain)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    @property
    def is_serverless(self) -> bool:
        """True no ambiente serverless da Vercel (funções efêmeras, Seção 2.1).

        A Vercel injeta ``VERCEL=1`` no runtime. Nesse modo o pool asyncpg deve
        rodar com ``min_size=0`` e teto restrito para não vazar conexões ociosas
        contra o Supavisor (ver ``app/db.py``).
        """
        return bool(os.environ.get("VERCEL"))

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

"""Configuração e segredos (Pydantic Settings, Seções 3.6 / 6.2).

Toda configuração vem de variáveis de ambiente. `.env` nunca é commitado;
em produção os segredos vêm do ambiente do Railway. Nenhuma chave é
hard-coded — ver REGRA DURA da Seção 6.2 do plano mestre.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SESSION_SECRET = "dev-insecure-session-secret-change-me"
_DEFAULT_CSRF_SECRET = "dev-insecure-csrf-secret-change-me"


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
    session_secret: str = Field(default=_DEFAULT_SESSION_SECRET)
    csrf_secret: str = Field(default=_DEFAULT_CSRF_SECRET)

    # --- Cookies ---
    cookie_secure: bool = Field(default=True)

    # --- Diagnóstico (Sprint 1 / item 1.2) ---
    # Token exigido em produção para acessar /health/ready e /health/config
    # (header X-Diagnostics-Token). Vazio ⇒ rotas negadas em produção (não há
    # como liberar por omissão — fail-closed).
    diagnostics_token: str = Field(default="")

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
    #     que SMTP no event loop assíncrono: sem socket TCP bloqueante, sem
    #     timeouts de STARTTLS, entrega por HTTPS. ---
    mailgun_api_key: str = Field(default="")
    mailgun_domain: str = Field(default="")
    # Região da conta: US = https://api.mailgun.net | EU = https://api.eu.mailgun.net
    mailgun_base_url: str = Field(default="https://api.mailgun.net")

    # --- WhatsApp Cloud API (Meta) ---
    # Token arbitrário definido por nós e colado no painel da Meta ("Verificar
    # token"); usado só no handshake GET de assinatura do webhook.
    whatsapp_verify_token: str = Field(default="")
    # App Secret do app da Meta, usado para validar a assinatura HMAC
    # (header X-Hub-Signature-256) de cada POST recebido no webhook.
    whatsapp_app_secret: str = Field(default="")

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

    @model_validator(mode="after")
    def _fail_fast_segredos_default_em_producao(self) -> "Settings":
        """Sprint 0 / item 0.2 (auditoria 2026-07-14): impossível subir produção
        com SESSION_SECRET/CSRF_SECRET no valor default de desenvolvimento —
        aborta o boot em vez de servir sessões/CSRF assinados com um segredo
        público (presente neste repo)."""
        if not self.is_production:
            return self
        defaults_em_uso = [
            nome
            for nome, valor, default in (
                ("SESSION_SECRET", self.session_secret, _DEFAULT_SESSION_SECRET),
                ("CSRF_SECRET", self.csrf_secret, _DEFAULT_CSRF_SECRET),
            )
            if valor == default
        ]
        if defaults_em_uso:
            raise ValueError(
                "Boot abortado: ambiente de produção com segredo(s) de "
                f"desenvolvimento não substituído(s): {', '.join(defaults_em_uso)}. "
                "Gere valores aleatórios (ex.: `openssl rand -hex 32`) e defina-os "
                "nas variáveis de ambiente antes de subir em produção."
            )
        return self

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

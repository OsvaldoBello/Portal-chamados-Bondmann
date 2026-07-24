"""Configuração e segredos (Pydantic Settings, Seções 3.6 / 6.2).

Toda configuração vem de variáveis de ambiente. `.env` nunca é commitado;
em produção os segredos vêm do ambiente do Railway. Nenhuma chave é
hard-coded — ver REGRA DURA da Seção 6.2 do plano mestre.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, model_validator
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
    # Token exigido em produção para acessar /health/ready, /health/config e
    # /metrics (header X-Diagnostics-Token). Vazio ⇒ rotas negadas em produção
    # (não há como liberar por omissão — fail-closed).
    diagnostics_token: str = Field(default="")

    # --- Observabilidade (Sprint 2 / item 2.6) ---
    # DSN do Sentry (ou compatível). Vazia ⇒ integração desligada por completo
    # (sentry_sdk.init() nunca roda; capture_exception() vira no-op).
    sentry_dsn: str = Field(default="")
    # Fração de requisições com tracing de performance (0.0–1.0). Erros são
    # sempre capturados independente deste valor; 0 é o padrão consciente de
    # custo (só liga tracing quando houver necessidade concreta de investigar
    # latência via Sentry, em vez do /metrics local).
    sentry_traces_sample_rate: float = Field(default=0.0)

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

    # --- IA (triagem de chamados + resumo do Químico) ---
    # Frente de IA de triagem (plano_md_mestre_IA.md, Seção 2.4): TODAS as flags
    # entram de uma vez com defaults DESLIGADOS — ligar/desligar fases seguintes
    # é só env no Railway, sem deploy. Provedor plugável via API compatível com
    # o formato OpenAI (/chat/completions); decisão C5 (2026-07-22): OpenAI /
    # gpt-5.4-mini (Groq descontinuado). Trocar de provedor = trocar
    # IA_TRIAGEM_BASE_URL + IA_TRIAGEM_MODEL.
    #
    # `[DECISÃO DE ENGENHARIA — F0]` rename groq_* → ia_triagem_* (C5) com
    # fallback de alias para os nomes antigos (GROQ_API_KEY/GROQ_MODEL/
    # GROQ_BASE_URL): o ambiente de produção que ainda tiver os envs antigos
    # continua funcionando até o gestor migrar os nomes no Railway.

    # Kill switch geral da TRIAGEM: false = nenhum agente roda (nem em sombra).
    # (Não afeta o resumo do Químico, que depende só da api_key — ver
    # `ia_resumo_ativo`.)
    ia_triagem_ativa: bool = Field(default=False)
    # CSV de nomes de departamentos com triagem (ex.: "TI" ou "TI,Dpto Químico").
    # Vazio = nenhum.
    ia_triagem_departamentos: str = Field(default="")
    # Modo sombra: o motor roda tudo, mas SÓ grava notas internas — nunca
    # mensagens públicas nem e-mail. É o modo da F1. `false` = ninguém em
    # sombra (estado final F6).
    ia_triagem_modo_sombra: bool = Field(default=True)
    # Saída da sombra POR DEPARTAMENTO (F2, 2026-07-24): CSV de departamentos
    # liberados para perguntas públicas MESMO com a sombra global ligada
    # (ex.: "TI") — permite o TI perguntar ao autor mantendo o Químico em
    # sombra até o red team (F5/F6, gate de zero vazamentos). Vazio = ninguém
    # sai; irrelevante quando `ia_triagem_modo_sombra=false`.
    ia_triagem_perguntas_departamentos: str = Field(default="")
    # Confiança mínima da saída do modelo para perguntar ao autor
    # (BAIXA/MEDIA/ALTA). Era ALTA fixa em código (mitigação 10.1); decisão do
    # usuário 2026-07-24 (BOND-2026-00601 saiu com BAIXA e não perguntou):
    # baixa e média também perguntam ⇒ default BAIXA. Reapertar é só env;
    # valor inválido degrada para ALTA (conservador).
    ia_triagem_perguntas_confianca_minima: str = Field(default="BAIXA")
    ia_triagem_model: str = Field(
        default="gpt-5.4-mini",
        validation_alias=AliasChoices("ia_triagem_model", "groq_model"),
    )
    # Opcional; vazio = usa `ia_triagem_model` (Passe B do Químico, F4).
    ia_triagem_model_passe_b: str = Field(default="")
    ia_triagem_base_url: str = Field(
        default="https://api.openai.com/v1",
        validation_alias=AliasChoices("ia_triagem_base_url", "groq_base_url"),
    )
    # Chave do provedor. Vazia = toda IA desligada (triagem E resumo). SÓ via
    # env — nunca em código/doc/commit (REGRA DURA Seção 6.2 + C5).
    ia_triagem_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ia_triagem_api_key", "groq_api_key"),
    )
    # Timeout por passe (C6: 30 s unificado; ia_resumo herda este valor).
    ia_triagem_timeout_s: float = Field(default=30.0)
    # Teto de rodadas de perguntas ao usuário (Regra de Ouro #3).
    ia_triagem_max_rodadas: int = Field(default=2)
    # Conexão do role Postgres `ia_worker` (C7) — obrigatória só para o
    # Químico (F4). Vazia = contexto sigiloso indisponível.
    ia_worker_database_url: str = Field(default="")
    # Intervalo (segundos) da varredura de reconciliação — rede de segurança
    # para triagens perdidas: `agendar_triagem` roda via `asyncio.create_task`,
    # que NÃO é durável (um restart/redeploy entre o agendamento e o primeiro
    # INSERT em `ia_triagens` apaga a tarefa sem deixar rastro — caso real do
    # BOND-2026-00593, 2026-07-23). `<= 0` desliga a varredura; o kill switch
    # geral (`ia_triagem_ativa`) também a desliga (Regra de Ouro #5).
    ia_triagem_reconciliacao_intervalo_s: float = Field(default=300.0)

    @property
    def ia_resumo_ativo(self) -> bool:
        """A geração de resumo por IA está configurada (há chave)?"""
        return bool(self.ia_triagem_api_key)

    @property
    def ia_triagem_departamentos_lista(self) -> list[str]:
        """Nomes de departamentos com triagem ativa (CSV → lista, sem vazios)."""
        return [d.strip() for d in self.ia_triagem_departamentos.split(",") if d.strip()]

    @property
    def ia_triagem_perguntas_departamentos_lista(self) -> list[str]:
        """Departamentos liberados para perguntas públicas (CSV → lista)."""
        return [
            d.strip() for d in self.ia_triagem_perguntas_departamentos.split(",") if d.strip()
        ]

    def ia_triagem_em_sombra(self, departamento_nome: str | None) -> bool:
        """O departamento está em modo sombra? (Seção 2.3 do plano IA.)

        Sombra global desligada ⇒ ninguém em sombra (F6). Ligada ⇒ só sai da
        sombra quem está em `IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS` (saída por
        departamento, F2 — o Químico fica em sombra até o red team, F5)."""
        if not self.ia_triagem_modo_sombra:
            return False
        return (departamento_nome or "") not in self.ia_triagem_perguntas_departamentos_lista

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
    def _fail_fast_segredos_default_em_producao(self) -> Settings:
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

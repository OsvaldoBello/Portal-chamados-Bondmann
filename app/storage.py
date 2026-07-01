"""Storage de anexos no Supabase Storage (bucket privado, Seção 3.9).

Acesso **direto à REST do Storage** via ``httpx`` autenticado com o **JWT do
usuário** — não pela `service_role` (que bypassa RLS) nem pelo supabase-py, cuja
superfície async de Storage é incompleta (Seção 1.4). Com o token do usuário, as
policies path-scoped ``{empresa_id}/{chamado_id}/{arquivo}`` são aplicadas pelo
próprio Supabase.

- **Upload:** ``POST /storage/v1/object/{bucket}/{path}``.
- **Signed URL (TTL 1h):** ``POST /storage/v1/object/sign/{bucket}/{path}`` —
  gerada *on-demand* a cada renderização, nunca cacheada além do TTL (C2).
"""

from __future__ import annotations

import logging

import httpx

from app.config import Settings

log = logging.getLogger("app.storage")

_storage: "AnexosStorage | None" = None


class StorageError(RuntimeError):
    """Falha ao falar com o Storage do Supabase."""


class AnexosStorage:
    def __init__(self, base_url: str, bucket: str, ttl: int, client: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._bucket = bucket
        self._ttl = ttl
        self._http = client

    @staticmethod
    def path(empresa_id: str, chamado_id: str, nome_objeto: str) -> str:
        """Convenção tenant-scoped exigida pelas policies (Seção 3.9)."""
        return f"{empresa_id}/{chamado_id}/{nome_objeto}"

    async def upload(self, token: str, path: str, conteudo: bytes, mime: str) -> None:
        url = f"{self._base}/storage/v1/object/{self._bucket}/{path}"
        resp = await self._http.post(
            url,
            content=conteudo,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": mime,
                # Bucket privado; não sobrescreve objeto existente (idempotência
                # reforçada pelo nome UUID já único por upload).
                "x-upsert": "false",
            },
        )
        if resp.status_code >= 400:
            log.warning("falha no upload de anexo", extra={"status": resp.status_code})
            raise StorageError(f"upload falhou ({resp.status_code})")

    async def signed_url(self, token: str, path: str) -> str | None:
        """Gera uma signed URL (TTL 1h) para o objeto. ``None`` em caso de falha
        (o template degrada para 'anexo indisponível')."""
        url = f"{self._base}/storage/v1/object/sign/{self._bucket}/{path}"
        try:
            resp = await self._http.post(
                url,
                json={"expiresIn": self._ttl},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError:
            log.exception("erro ao assinar URL de anexo")
            return None
        if resp.status_code >= 400:
            return None
        signed = resp.json().get("signedURL") or resp.json().get("signedUrl")
        if not signed:
            return None
        return f"{self._base}/storage/v1{signed}"


async def init_storage(settings: Settings) -> AnexosStorage:
    global _storage
    if _storage is None:
        client = httpx.AsyncClient(timeout=30.0)
        _storage = AnexosStorage(
            base_url=settings.supabase_url,
            bucket=settings.anexos_bucket,
            ttl=settings.signed_url_ttl,
            client=client,
        )
    return _storage


async def close_storage() -> None:
    global _storage
    if _storage is not None:
        http = getattr(_storage, "_http", None)
        if http is not None:
            await http.aclose()
        _storage = None


def get_storage() -> AnexosStorage:
    if _storage is None:
        raise RuntimeError("Storage não inicializado (Supabase configurado?).")
    return _storage


async def ensure_storage() -> "AnexosStorage | None":
    """Garante o storage mesmo sem lifespan (serverless). Retorna None se o
    Supabase não estiver configurado (uploads/anexos degradam graciosamente)."""
    if _storage is None:
        from app.config import get_settings

        settings = get_settings()
        if settings.supabase_url:
            await init_storage(settings)
    return _storage

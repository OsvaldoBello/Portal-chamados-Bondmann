"""Storage do avatar de usuário — bucket público `avatares` (Fase 7, 2026-07-09).

Diferente dos anexos (`app/storage.py`, bucket privado + signed URL regerada a
cada render), avatar não é dado sensível e é renderizado em muitas células de
card ao mesmo tempo (fila/kanban/chamados do setor) — bucket PÚBLICO: URL
direta e estável, sem custo de assinatura por render. Upload segue exigindo o
JWT do usuário (a RLS de `storage.objects`, migration 0033, só deixa cada um
escrever no próprio path).
"""

from __future__ import annotations

from io import BytesIO

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from app.config import Settings, get_settings
from app.security.uploads import UploadInvalido, validar_anexo

# Lado (em px) do quadrado final — fixo para toda foto de perfil, independente
# da resolução enviada (Fase 7). Mantém o bucket leve e a bolinha nítida sem
# depender do cliente enviar algo já quadrado.
_AVATAR_LADO = 512
_EXTENSOES_AVATAR = {"jpg", "jpeg", "png"}


class AvatarStorageError(RuntimeError):
    """Falha ao enviar o avatar para o Storage do Supabase."""


def avatar_path(user_id: str, ext: str) -> str:
    """Path fixo por usuário (`{user_id}/avatar.<ext>`) — reenviar SUBSTITUI o
    avatar anterior (upload com `x-upsert`), não acumula arquivo por upload."""
    return f"{user_id}/avatar.{ext}"


def _recortar_quadrado(conteudo: bytes) -> bytes:
    """Recorta a imagem num quadrado centralizado e normaliza para PNG.

    O template já exibe o avatar como bolinha via CSS (`rounded-full
    object-cover`), o que funciona visualmente para qualquer proporção — mas
    aqui garantimos o recorte de fato no arquivo persistido: independe da
    página/tela que o exibe (inclusive fora do CSS, ex.: cliente de e-mail) e
    evita guardar a foto original em alta resolução/proporção arbitrária no
    Storage público. ``ImageOps.exif_transpose`` corrige fotos de celular que
    vêm giradas via metadado EXIF em vez de pixels."""
    try:
        with Image.open(BytesIO(conteudo)) as img:
            img = ImageOps.exif_transpose(img) or img
            img = img.convert("RGBA")
            lado = min(img.size)
            esquerda = (img.width - lado) // 2
            topo = (img.height - lado) // 2
            img = img.crop((esquerda, topo, esquerda + lado, topo + lado))
            img = img.resize((_AVATAR_LADO, _AVATAR_LADO), Image.Resampling.LANCZOS)
            saida = BytesIO()
            img.save(saida, format="PNG")
            return saida.getvalue()
    except (UnidentifiedImageError, OSError) as exc:
        raise UploadInvalido(
            "Não foi possível processar a imagem. Envie um JPG ou PNG válido."
        ) from exc


def preparar_avatar(nome: str, conteudo: bytes, *, max_bytes: int) -> bytes:
    """Valida o upload (allow-list/magic bytes/tamanho, `app/security/uploads.py`)
    e devolve a foto já recortada em quadrado e normalizada para PNG. Levanta
    ``UploadInvalido`` em qualquer falha — tipo não suportado ou imagem
    corrompida/ilegível."""
    validado = validar_anexo(nome, conteudo, max_bytes=max_bytes)
    if validado.ext not in _EXTENSOES_AVATAR:
        raise UploadInvalido("Envie uma imagem JPG ou PNG.")
    return _recortar_quadrado(validado.conteudo)


def avatar_public_url(
    path: str, atualizado_em: object = None, *, settings: Settings | None = None
) -> str | None:
    """URL pública e estável do avatar (bucket público, sem assinatura).

    ``atualizado_em`` (o `perfis.updated_at` do dono do avatar, já bumped pelo
    trigger `set_timestamp_perfis` a cada reenvio) vira um ``?v=`` de
    cache-busting: o path em si é **fixo** por usuário (`{user_id}/avatar.png`,
    reenviar substitui — Fase 7), então sem isso o navegador segue mostrando a
    foto antiga em cache depois de um reenvio, já que a URL não muda."""
    if not path:
        return None
    settings = settings or get_settings()
    if not settings.supabase_url:
        return None
    base = settings.supabase_url.rstrip("/")
    url = f"{base}/storage/v1/object/public/{settings.avatares_bucket}/{path}"
    # Checagem por veracidade (não `is not None`): quando o template não tiver
    # esse campo (dict/fake sem a chave), o Jinja passa seu próprio sentinela
    # `Undefined` — que é *falsy* mas não é `None` e explode em qualquer
    # acesso de atributo (inclusive via `hasattr`). Sem cache-busting aqui é
    # só voltar à URL estável de sempre, não um erro de render.
    if atualizado_em:
        versao = int(atualizado_em.timestamp()) if hasattr(atualizado_em, "timestamp") else atualizado_em
        url = f"{url}?v={versao}"
    return url


async def enviar_avatar(
    token: str, path: str, conteudo: bytes, mime: str, *, settings: Settings | None = None
) -> None:
    """Envia o avatar já validado (`app/security/uploads.py:validar_anexo`) ao
    Storage. Upload pontual e raro — não justifica um cliente httpx persistente
    como o de anexos (Seção 1.4: `anyio.to_thread`/pool só valem a pena para I/O
    de alta frequência)."""
    settings = settings or get_settings()
    base = settings.supabase_url.rstrip("/")
    url = f"{base}/storage/v1/object/{settings.avatares_bucket}/{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            content=conteudo,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": mime,
                "x-upsert": "true",
            },
        )
    if resp.status_code >= 400:
        raise AvatarStorageError(f"upload de avatar falhou ({resp.status_code})")

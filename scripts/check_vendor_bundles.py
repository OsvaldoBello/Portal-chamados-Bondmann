"""Guarda os bundles vendorizados de `app/static/vendor/` contra deriva.

Achado da auditoria de cadeia de suprimentos (2026-08-26): esses arquivos são
cópias **manuais** commitadas, não saída de build. Nada ligava o `package.json`
ao que o browser realmente carrega, então um PR do Dependabot bumpava o
manifesto e aparentava corrigir uma CVE sem tocar no arquivo servido — foi
exatamente o que aconteceu com o `@supabase/supabase-js` 2.47.10, que carregava
uma `auth-js` vulnerável (GHSA-8r88-6cj9-9fh5) muito depois de existir correção.

Duas invariantes, as duas silenciosas quando quebram:

1. **Versão.** A versão embutida no bundle bate com o pin do `package.json`.
   Sem isto, manifesto e browser divergem sem aviso.
2. **SRI.** O `integrity="sha384-…"` de cada `<script>` nos templates bate com o
   hash do arquivo. Sem isto o browser *bloqueia* o script — o Realtime (sino de
   notificações e chat) simplesmente para, sem erro visível ao usuário.

Roda sem dependência externa (stdlib), como os outros `scripts/check_*.py`.
Uso: `python scripts/check_vendor_bundles.py` — sai 1 e lista o que divergiu.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
_VENDOR = _RAIZ / "app" / "static" / "vendor"
_TEMPLATES = _RAIZ / "app" / "templates"

# arquivo vendorizado -> (pacote npm, regex que captura a versão dentro do bundle)
# Cada lib embute a versão de um jeito; o minificador do supabase-js usa crase.
_BUNDLES: dict[str, tuple[str, str]] = {
    "supabase.min.js": ("@supabase/supabase-js", r"supabase-js/(\d+\.\d+\.\d+)"),
    "htmx.min.js": ("htmx.org", r"version:[\"'`](\d+\.\d+\.\d+)"),
    "alpine-csp.min.js": ("@alpinejs/csp", r"version:[\"'`](\d+\.\d+\.\d+)"),
    "chart.umd.js": ("chart.js", r"version=[\"'`](\d+\.\d+\.\d+)"),
    "sortable.min.js": ("sortablejs", r"version=[\"'`](\d+\.\d+\.\d+)"),
}

_TAG_SCRIPT = re.compile(
    r"<script\s+src=\"/static/vendor/(?P<arquivo>[\w.-]+)\"\s+"
    r"integrity=\"(?P<sri>sha384-[A-Za-z0-9+/=]+)\"",
    re.DOTALL,
)


def _sri(caminho: Path) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(caminho.read_bytes()).digest()).decode()


def _pin(manifesto: dict, pacote: str) -> str | None:
    for secao in ("dependencies", "devDependencies"):
        if pacote in manifesto.get(secao, {}):
            # aceita `^1.2.3` e `1.2.3`; o que importa é a versão-alvo
            return manifesto[secao][pacote].lstrip("^~=")
    return None


def checar_versoes(manifesto: dict) -> list[str]:
    erros: list[str] = []
    for arquivo, (pacote, padrao) in _BUNDLES.items():
        caminho = _VENDOR / arquivo
        if not caminho.exists():
            erros.append(f"{arquivo}: ausente em app/static/vendor/")
            continue
        pin = _pin(manifesto, pacote)
        if pin is None:
            erros.append(f"{arquivo}: `{pacote}` não está no package.json")
            continue
        achado = re.search(padrao, caminho.read_text(encoding="utf-8", errors="ignore"))
        if achado is None:
            erros.append(
                f"{arquivo}: versão não encontrada no bundle (padrão {padrao!r}). "
                "Se o upstream mudou o formato da string, ajuste _BUNDLES."
            )
        elif achado.group(1) != pin:
            erros.append(
                f"{arquivo}: bundle tem {achado.group(1)}, package.json pin {pin}. "
                f"Recopie de node_modules/{pacote}/dist/ (`npm run vendor:sync`)."
            )
    return erros


def checar_sri() -> list[str]:
    erros: list[str] = []
    vistos = 0
    for template in sorted(_TEMPLATES.rglob("*.html")):
        html = template.read_text(encoding="utf-8")
        for tag in _TAG_SCRIPT.finditer(html):
            vistos += 1
            arquivo = tag.group("arquivo")
            caminho = _VENDOR / arquivo
            rel = template.relative_to(_RAIZ).as_posix()
            if not caminho.exists():
                erros.append(f"{rel}: aponta para vendor/{arquivo}, que não existe")
                continue
            esperado = _sri(caminho)
            if tag.group("sri") != esperado:
                erros.append(
                    f"{rel}: integrity de vendor/{arquivo} desatualizado.\n"
                    f"    no template: {tag.group('sri')}\n"
                    f"    do arquivo:  {esperado}\n"
                    "    O browser BLOQUEIA o script com o hash errado — "
                    "o Realtime para sem erro visível."
                )
    if vistos == 0:
        erros.append(
            "nenhum <script src=/static/vendor/... integrity=...> encontrado nos "
            "templates; ou o SRI foi removido, ou o formato da tag mudou e este "
            "check virou um no-op silencioso."
        )
    return erros


def main() -> int:
    manifesto = json.loads((_RAIZ / "package.json").read_text(encoding="utf-8"))
    erros = checar_versoes(manifesto) + checar_sri()
    if erros:
        print("Bundles vendorizados divergem:\n")
        for erro in erros:
            print(f"  - {erro}")
        print(
            "\nOs arquivos de app/static/vendor/ sao copias manuais: bump no "
            "package.json NAO atualiza o que o browser carrega."
        )
        return 1
    print(
        f"OK: {len(_BUNDLES)} bundles batem com o pin do package.json e "
        "todo integrity= dos templates bate com o arquivo."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

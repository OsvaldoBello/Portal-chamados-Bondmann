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

# Casa a tag <script ...> inteira (até o `>` de fechamento da abertura) e
# procura `src=`/`integrity=` como atributos independentes dentro dela — não
# lado a lado. Versão anterior exigia `src="..."` seguido imediatamente de
# `integrity="..."`, e não via `<script defer src=... integrity=...>` (o
# `defer` no meio quebrava o casamento): htmx.min.js e alpine-csp.min.js
# nunca tiveram o SRI checado de verdade desde que este script existe — os
# outros 3 bundles não usam `defer` na tag, por isso o "OK" saía sem avisar.
_TAG_SCRIPT = re.compile(r"<script\b[^>]*>", re.DOTALL)
_ATTR_SRC = re.compile(r'src="/static/vendor/(?P<arquivo>[\w.-]+)"')
_ATTR_SRI = re.compile(r'integrity="(?P<sri>sha384-[A-Za-z0-9+/=]+)"')


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
    vistos: set[str] = set()
    for template in sorted(_TEMPLATES.rglob("*.html")):
        html = template.read_text(encoding="utf-8")
        for tag_completa in _TAG_SCRIPT.findall(html):
            src = _ATTR_SRC.search(tag_completa)
            if src is None:
                continue  # <script> sem src pro vendor/ — não é desta checagem
            arquivo = src.group("arquivo")
            vistos.add(arquivo)
            caminho = _VENDOR / arquivo
            rel = template.relative_to(_RAIZ).as_posix()
            if not caminho.exists():
                erros.append(f"{rel}: aponta para vendor/{arquivo}, que não existe")
                continue
            sri = _ATTR_SRI.search(tag_completa)
            esperado = _sri(caminho)
            if sri is None:
                erros.append(
                    f"{rel}: <script src=/static/vendor/{arquivo}> sem atributo "
                    f"integrity= nenhum (esperado {esperado})."
                )
            elif sri.group("sri") != esperado:
                erros.append(
                    f"{rel}: integrity de vendor/{arquivo} desatualizado.\n"
                    f"    no template: {sri.group('sri')}\n"
                    f"    do arquivo:  {esperado}\n"
                    "    O browser BLOQUEIA o script com o hash errado — "
                    "o Realtime para sem erro visível."
                )
    if not vistos:
        erros.append(
            "nenhum <script src=/static/vendor/...> encontrado nos templates; "
            "ou os bundles pararam de ser usados, ou o formato da tag mudou e "
            "este check virou um no-op silencioso."
        )
    faltando = sorted(set(_BUNDLES) - vistos)
    if faltando:
        erros.append(
            f"{', '.join(faltando)} nunca aparece(m) num <script src=/static/vendor/...> "
            "de nenhum template — ou o bundle não é mais usado (remova de _BUNDLES), "
            "ou a tag mudou de formato de um jeito que este regex não reconhece "
            "(foi exatamente assim que o SRI de htmx.min.js/alpine-csp.min.js ficou "
            "sem checagem por um PR inteiro: a tag tinha `defer` entre `<script` e "
            "`src=`, e o regex antigo exigia os dois lado a lado)."
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

"""Recopia os bundles de `app/static/vendor/` a partir de `node_modules/` e
regenera o `integrity=` dos templates.

Contraparte de `check_vendor_bundles.py`: o check reprova a deriva, este script
a corrige. Antes disto o procedimento era manual e de três passos — bump no
`package.json`, copiar o arquivo, recalcular o SRI — e esquecer o terceiro faz
o browser **bloquear** o script, derrubando o Realtime sem erro visível.

Uso: `npm run vendor:sync` (ou `python scripts/sync_vendor_bundles.py`), depois
do `npm install`. Sem argumento sincroniza tudo; com nomes de arquivo, só eles.
`--check` não escreve nada e só relata o que mudaria.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import shutil
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
_VENDOR = _RAIZ / "app" / "static" / "vendor"
_TEMPLATES = _RAIZ / "app" / "templates"
_NODE = _RAIZ / "node_modules"

# arquivo em app/static/vendor/ -> caminho da build UMD/browser dentro do pacote
_ORIGENS: dict[str, str] = {
    "supabase.min.js": "@supabase/supabase-js/dist/umd/supabase.js",
    "htmx.min.js": "htmx.org/dist/htmx.min.js",
    "alpine-csp.min.js": "@alpinejs/csp/dist/cdn.min.js",
    "chart.umd.js": "chart.js/dist/chart.umd.js",
    "sortable.min.js": "sortablejs/Sortable.min.js",
}


def _sri(dados: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(dados).digest()).decode()


def _atualizar_sri(arquivo: str, sri: str, escrever: bool) -> list[str]:
    """Reescreve o integrity= de `arquivo` em todo template que o carrega."""
    padrao = re.compile(
        rf"(<script\s+src=\"/static/vendor/{re.escape(arquivo)}\"\s+integrity=\")"
        r"sha384-[A-Za-z0-9+/=]+(\")"
    )
    tocados: list[str] = []
    for template in sorted(_TEMPLATES.rglob("*.html")):
        html = template.read_text(encoding="utf-8")
        novo, n = padrao.subn(rf"\g<1>{sri}\g<2>", html)
        if n and novo != html:
            tocados.append(f"{template.relative_to(_RAIZ).as_posix()} ({n}x)")
            if escrever:
                template.write_text(novo, encoding="utf-8")
    return tocados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arquivos", nargs="*", help="quais sincronizar (default: todos)")
    ap.add_argument("--check", action="store_true", help="não escreve; só relata")
    args = ap.parse_args()

    alvos = args.arquivos or list(_ORIGENS)
    desconhecidos = [a for a in alvos if a not in _ORIGENS]
    if desconhecidos:
        print(f"erro: sem origem mapeada para {', '.join(desconhecidos)}", file=sys.stderr)
        return 2

    escrever = not args.check
    mudou = False
    for arquivo in alvos:
        origem = _NODE / _ORIGENS[arquivo]
        destino = _VENDOR / arquivo
        if not origem.exists():
            print(f"erro: {origem.relative_to(_RAIZ).as_posix()} não existe — rode `npm install`.")
            return 1
        dados = origem.read_bytes()
        igual = destino.exists() and destino.read_bytes() == dados
        sri = _sri(dados)
        if igual:
            tocados = _atualizar_sri(arquivo, sri, escrever)
            if tocados:
                mudou = True
                print(f"{arquivo}: conteúdo já em dia, integrity= corrigido em {', '.join(tocados)}")
            continue
        mudou = True
        if escrever:
            shutil.copyfile(origem, destino)
        tocados = _atualizar_sri(arquivo, sri, escrever)
        verbo = "copiado de" if escrever else "copiaria de"
        print(f"{arquivo}: {verbo} {_ORIGENS[arquivo]}")
        print(f"    integrity= {sri}")
        print(f"    templates: {', '.join(tocados) if tocados else 'nenhum carrega este arquivo'}")

    if not mudou:
        print(f"OK: {len(alvos)} bundle(s) já em dia com node_modules/.")
    elif args.check:
        print("\n--check: nada foi escrito.")
        return 1
    else:
        print("\nRode `python scripts/check_vendor_bundles.py` para confirmar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Checagem de numeração contínua de `supabase/migrations/` (Sprint 0 / item 0.4).

Teria pegado o buraco da migration 0015 (item 0.1 do plano de melhorias):
falha se a sequência `NNNN_*.sql` tiver lacuna, duplicata ou não começar em 1.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
_NOME_RE = re.compile(r"^(\d{4})_.+\.sql$")


def main() -> int:
    arquivos = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    numeros: list[int] = []
    invalidos: list[str] = []

    for arquivo in arquivos:
        m = _NOME_RE.match(arquivo.name)
        if not m:
            invalidos.append(arquivo.name)
            continue
        numeros.append(int(m.group(1)))

    if invalidos:
        print("Arquivos fora do padrão NNNN_nome.sql:")
        for nome in invalidos:
            print(f"  - {nome}")
        return 1

    if not numeros:
        print(f"Nenhuma migration encontrada em {_MIGRATIONS_DIR}")
        return 1

    numeros.sort()
    duplicatas = {n for n in numeros if numeros.count(n) > 1}
    if duplicatas:
        print(f"Números duplicados: {sorted(duplicatas)}")
        return 1

    esperado = list(range(1, numeros[-1] + 1))
    faltando = sorted(set(esperado) - set(numeros))
    if faltando:
        print(f"Lacuna na numeração — faltando: {faltando}")
        return 1

    print(f"OK: {len(numeros)} migrations, sequência 0001..{numeros[-1]:04d} contínua.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

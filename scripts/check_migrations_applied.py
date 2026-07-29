#!/usr/bin/env python3
"""Checagem de migrations do repositório que ainda NÃO foram aplicadas no banco.

Por que existe (achado da revisão de CI/CD, 2026-07-29): nem o CI nem o deploy
do Railway aplicam `supabase/migrations/*.sql` — isso é passo manual (painel do
Supabase / MCP). Como o Railway sobe o código assim que o push acontece, uma
migration esquecida abre uma janela em que a aplicação nova roda contra o schema
velho. Já aconteceu: `0060`/`0061` ficaram registradas como "não aplicadas em
produção" no CHANGELOG com o código correspondente já mergeado.

Este script NÃO aplica nada — é um portão. Ele reprova o build quando existe
migration nova no repositório sem linha correspondente em
`supabase_migrations.schema_migrations`, e a aplicação continua deliberada e
manual (DDL automático em produção a cada push seria o remédio pior que a
doença). Com o gate ligado, a ordem correta fica forçada: aplica o schema,
o check fica verde, o código entra.

Por que não `supabase db push`/`supabase migration list`: a CLI casa local com
remoto pela VERSÃO (timestamp) do nome do arquivo, e este repositório usa a
convenção própria `NNNN_descricao.sql` (Seção 5.4 do plano mestre). Para a CLI,
toda migration local apareceria como pendente. O casamento aqui é por NOME.

BASELINE: o histórico remoto é incompleto para as migrations antigas — várias
foram aplicadas direto no SQL editor antes de existir registro (0018–0024,
0026–0028, 0045, 0046 não têm linha), e outras foram registradas só com o slug,
sem o prefixo numérico (`ia_triagens` para a `0050_ia_triagens.sql`). Conferir
o passado daria falso-positivo eterno, então o portão vale da `_BASELINE` para
cima: o estado de 2026-07-29, com a `0064` aplicada e conferida. Migration nova
entra acima disso e é checada.

Uso:
    SUPABASE_DB_URL=postgresql://... python scripts/check_migrations_applied.py

Sem `SUPABASE_DB_URL` (nem `DATABASE_URL`), o script AVISA e sai com 0 — para o
CI de quem não tem o secret configurado não travar, e para o `pytest` de quem
clonou o repositório continuar independente de banco.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

import asyncpg

_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "supabase" / "migrations"
_NOME_RE = re.compile(r"^(\d{4})_(.+)\.sql$")

# Última migration comprovadamente aplicada em produção quando o portão nasceu.
# Sobe junto com a realidade só se um dia o histórico antigo for reconciliado —
# não é para ser bumpado a cada migration nova (seria desligar o portão).
_BASELINE = 64


async def _nomes_remotos(dsn: str) -> set[str]:
    # `statement_cache_size=0` é obrigatório sob o pooler em transaction mode
    # (Supavisor/pgbouncer), o mesmo motivo de `app/db.py::get_pool` — sem isso,
    # a segunda execução estoura com `prepared statement "__asyncpg_stmt_N__"
    # already exists`. A URL de produção passa pelo pooler.
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        linhas = await conn.fetch(
            "SELECT name FROM supabase_migrations.schema_migrations WHERE name IS NOT NULL"
        )
    finally:
        await conn.close()
    return {linha["name"] for linha in linhas}


def _pendentes(remotos: set[str]) -> list[str]:
    faltando = []
    for arquivo in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        m = _NOME_RE.match(arquivo.name)
        if not m or int(m.group(1)) <= _BASELINE:
            continue
        stem, slug = arquivo.stem, m.group(2)
        # Registro pode ter sido gravado com o nome completo (`0064_sla_projetos_um_mes`)
        # ou só com o slug (`chamado_telefone_contato`, quando aplicada via MCP).
        if stem not in remotos and slug not in remotos:
            faltando.append(arquivo.name)
    return faltando


async def main() -> int:
    dsn = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print(
            "AVISO: SUPABASE_DB_URL/DATABASE_URL não configurada — checagem de "
            "migrations aplicadas pulada."
        )
        return 0

    try:
        remotos = await _nomes_remotos(dsn)
    except Exception as exc:  # noqa: BLE001 — falha de conexão não deve ser um "reprovado" silencioso
        print(f"ERRO: não foi possível consultar o histórico de migrations: {exc}")
        return 1

    faltando = _pendentes(remotos)
    if faltando:
        print("Migrations no repositório que NÃO estão aplicadas no banco:")
        for nome in faltando:
            print(f"  - {nome}")
        print(
            "\nAplique-as antes de subir o código que depende delas (painel do "
            "Supabase, MCP ou psql) e registre em docs/CHANGELOG.md."
        )
        return 1

    print(f"OK: nenhuma migration acima da baseline {_BASELINE:04d} pendente de aplicação.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

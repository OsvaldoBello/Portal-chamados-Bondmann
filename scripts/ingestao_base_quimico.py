"""CLI de ingestão da base do Dpto Químico (F4).

Fino wrapper de linha de comando sobre :mod:`app.services.ingestao_quimico`
(onde vive toda a lógica — reaproveitada pelo painel web `/admin/base-quimico`).
Rode localmente pelo dev/gestor apontando os arquivos (que ficam FORA do repo):

    python scripts/ingestao_base_quimico.py \
        --planilha "C:/caminho/Estrutura_de_Produtos_BASE_IA....xlsx" \
        --fichas-pdf "C:/caminho/Fichas Técnicas.pdf" \
        [--dsn postgresql://...]   # default: env INGESTAO_DATABASE_URL ou DATABASE_URL
        [--remover-ausentes]       # exclui produtos que saíram da planilha

Requer `openpyxl`/`pypdf` (agora em requirements.txt — o painel web também os usa).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Garante que `app` seja importável ao rodar o script direto (python scripts/...).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ingestao_quimico import RelatorioIngestao, ingerir_conn  # noqa: E402


async def _ingerir_dsn(
    dsn: str, planilha_bytes: bytes, pdf_bytes: bytes | None, *, remover_ausentes: bool
) -> RelatorioIngestao:
    """Abre conexão+transação próprias (asyncpg direto) e chama ``ingerir_conn``."""
    import asyncpg

    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        async with conn.transaction():
            return await ingerir_conn(
                conn, planilha_bytes, pdf_bytes, remover_ausentes=remover_ausentes
            )
    finally:
        await conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--planilha", required=True, help="Caminho do .xlsx (fora do repo)")
    parser.add_argument("--fichas-pdf", help="Caminho do PDF de fichas técnicas (fora do repo)")
    parser.add_argument(
        "--dsn",
        default=os.environ.get("INGESTAO_DATABASE_URL") or os.environ.get("DATABASE_URL"),
        help="DSN administrativa do Postgres (default: INGESTAO_DATABASE_URL ou DATABASE_URL)",
    )
    parser.add_argument(
        "--remover-ausentes",
        action="store_true",
        help="Excluir do banco produtos que NÃO estão na planilha (cascata: fichas + formulações).",
    )
    args = parser.parse_args(argv)
    if not args.dsn:
        parser.error("--dsn ausente e nem INGESTAO_DATABASE_URL/DATABASE_URL definidas.")

    planilha = Path(args.planilha)
    if not planilha.is_file():
        parser.error(f"Planilha não encontrada: {planilha}")
    pdf_bytes = None
    if args.fichas_pdf:
        fichas_pdf = Path(args.fichas_pdf)
        if not fichas_pdf.is_file():
            parser.error(f"PDF não encontrado: {fichas_pdf}")
        pdf_bytes = fichas_pdf.read_bytes()

    print(f"Lendo planilha: {planilha.name}" + (f" + {args.fichas_pdf}" if args.fichas_pdf else ""))
    rel = asyncio.run(
        _ingerir_dsn(
            args.dsn, planilha.read_bytes(), pdf_bytes, remover_ausentes=args.remover_ausentes
        )
    )
    print("Upsert concluído:", ", ".join(f"{k}={v}" for k, v in rel.contagens.items()))
    if rel.fichas_esperadas:
        print("  Fichas sem produto na planilha (esperado — ver runbook d.4):")
        for pagina, ancora, motivo in rel.fichas_esperadas:
            print(f"    pág {pagina}: {ancora} — {motivo}")
    if rel.fichas_inesperadas:
        print("  ⚠️ INESPERADO — fichas sem produto e sem tratamento (revisar):")
        for pagina, ancora in rel.fichas_inesperadas:
            print(f"    pág {pagina}: {ancora or '(página não identificada)'}")
    if rel.produtos_ausentes:
        rotulo = "REMOVIDOS" if args.remover_ausentes else "no banco, ausentes da planilha (mantidos)"
        print(f"  Produtos {rotulo}: {len(rel.produtos_ausentes)} — {', '.join(rel.produtos_ausentes)}")
    print("Re-execução é segura: mesma chave natural ⇒ UPDATE, nunca duplica.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

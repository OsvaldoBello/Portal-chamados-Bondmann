"""Monitor ao vivo do intake de chamados via WhatsApp (uso manual/dev).

Faz polling em `whatsapp_mensagens_recebidas`, `whatsapp_conversas`,
`ia_whatsapp_intake` e `chamados` via DATABASE_URL do `.env` (produção —
não há staging separado) e imprime uma linha por evento novo desde que o
script iniciou. `statement_cache_size=0` é exigido pelo Supavisor em
transaction mode (mesma exigência de app/db.py).

Uso: python scripts/monitor_whatsapp_intake.py [--interval 3]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
from datetime import UTC, datetime

import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Console do Windows costuma estar em cp1252 — respostas do modelo trazem
# emoji/acentos que quebram print() sem isto.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("DATABASE_URL nao encontrada no .env", file=sys.stderr)
    sys.exit(1)


def _log(msg: str) -> None:
    ts = datetime.now(UTC).astimezone().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def main(interval: float, since_minutes: float = 0.0) -> None:
    from datetime import timedelta

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
    start = datetime.now(UTC) - timedelta(minutes=since_minutes)
    _log(f"Monitor ligado. Observando eventos desde {start.isoformat()}.")

    seen_msgs: set[int] = set()
    seen_rodadas: set[int] = set()
    seen_chamados: set[str] = set()
    conversa_status: dict[str, str] = {}

    async def _reconectar() -> None:
        """O Supavisor derruba conexão ociosa depois de algumas horas — sem
        isto o monitor morria com `ConnectionDoesNotExistError` no meio de
        uma sessão de teste e parava de registrar tudo em silêncio (perdi um
        bug reportado pelo gestor assim, 2026-08-24). Os `seen_*` ficam de
        fora do reconnect de propósito: são memória do processo, então nada é
        reimpresso duas vezes."""
        nonlocal conn
        for tentativa in range(1, 7):
            try:
                with contextlib.suppress(Exception):
                    await conn.close()
                conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)
                _log("Conexão restabelecida.")
                return
            except Exception as exc:  # noqa: BLE001 — monitor não pode morrer
                espera = min(2**tentativa, 60)
                _log(f"Reconexão falhou ({exc}); tentando de novo em {espera}s.")
                await asyncio.sleep(espera)
        raise RuntimeError("não consegui reconectar ao banco")

    try:
        while True:
            try:
                msgs = await conn.fetch(
                    """
                    select id, telefone, tipo, corpo, status, created_at
                    from whatsapp_mensagens_recebidas
                    where created_at >= $1
                    order by created_at
                    """,
                    start,
                )
                for m in msgs:
                    if m["id"] in seen_msgs:
                        continue
                    seen_msgs.add(m["id"])
                    corpo = (m["corpo"] or "").replace("\n", " ")[:80]
                    _log(
                        f"MSG RECEBIDA tel={m['telefone']} tipo={m['tipo']} "
                        f"status={m['status']} corpo=\"{corpo}\""
                    )

                convs = await conn.fetch(
                    """
                    select id, telefone, status, rodada, chamado_id, atualizada_em
                    from whatsapp_conversas
                    where atualizada_em >= $1
                    order by atualizada_em
                    """,
                    start,
                )
                for c in convs:
                    cid = str(c["id"])
                    prev = conversa_status.get(cid)
                    cur = f"{c['status']}#{c['rodada']}"
                    if prev == cur:
                        continue
                    conversa_status[cid] = cur
                    extra = f" chamado_id={c['chamado_id']}" if c["chamado_id"] else ""
                    _log(
                        f"CONVERSA {cid[:8]} tel={c['telefone']} status={c['status']} "
                        f"rodada={c['rodada']}{extra}"
                    )

                rodadas = await conn.fetch(
                    """
                    select id, conversa_id, rodada, acao, modelo, custo_usd,
                           duracao_ms, resultado, created_at
                    from ia_whatsapp_intake
                    where created_at >= $1
                    order by created_at
                    """,
                    start,
                )
                for r in rodadas:
                    if r["id"] in seen_rodadas:
                        continue
                    seen_rodadas.add(r["id"])
                    _log(
                        f"IA rodada={r['rodada']} conversa={str(r['conversa_id'])[:8]} "
                        f"acao={r['acao']} modelo={r['modelo']} "
                        f"custo=${r['custo_usd']} dur={r['duracao_ms']}ms"
                    )
                    res = r["resultado"]
                    if isinstance(res, str):
                        try:
                            res = json.loads(res)
                        except json.JSONDecodeError:
                            res = {}
                    if isinstance(res, dict):
                        trilha = " > ".join(
                            str(res[k])
                            for k in ("setor", "departamento", "categoria", "subcategoria")
                            if res.get(k)
                        )
                        if trilha:
                            print(f"      rota: {trilha}", flush=True)
                        for p in res.get("perguntas") or []:
                            print(f"      PERGUNTA: {p}", flush=True)
                        campos = res.get("campos_formulario") or {}
                        if campos:
                            print("      formulario:", flush=True)
                            for nome, valor in campos.items():
                                print(f"        - {nome}: {valor}", flush=True)

                chamados = await conn.fetch(
                    """
                    select ch.id, ch.codigo, ch.titulo, ch.descricao,
                           d.nome as departamento,
                           c.nome as categoria, s.nome as subcategoria
                    from chamados ch
                    join whatsapp_conversas wc on wc.chamado_id = ch.id
                    left join departamentos d on d.id = ch.departamento_id
                    left join categorias c on c.id = ch.categoria_id
                    left join subcategorias s on s.id = ch.subcategoria_id
                    where ch.created_at >= $1
                    order by ch.created_at
                    """,
                    start,
                )
                for ch in chamados:
                    cid = str(ch["id"])
                    if cid in seen_chamados:
                        continue
                    seen_chamados.add(cid)
                    desc = (ch["descricao"] or "").replace("\n", " | ")[:200]
                    _log(
                        f"*** CHAMADO CRIADO {ch['codigo']} dept={ch['departamento']} "
                        f"cat={ch['categoria']} sub={ch['subcategoria']}\n"
                        f"      titulo: {ch['titulo']}\n"
                        f"      descricao: {desc}"
                    )
            except (asyncpg.PostgresConnectionError, ConnectionError, OSError) as exc:
                _log(f"Conexão caiu ({type(exc).__name__}: {exc}); reconectando.")
                await _reconectar()
                continue

            await asyncio.sleep(interval)
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument(
        "--since-minutes",
        type=float,
        default=0.0,
        help="Considera eventos desde N minutos atras (recuperar apos crash/restart)",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.interval, args.since_minutes))
    except KeyboardInterrupt:
        pass

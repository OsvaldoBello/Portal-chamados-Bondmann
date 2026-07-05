"""Teste bruto de concorrência (sem Locust) — Fase 5.

Dispara muitas requisições simultâneas contra um endpoint (default /health) e
reporta taxa de erro e percentis de latência. Serve como checagem rápida de que
o servidor não "quebra" sob rajada — complementa o teste de carga realista do
`locustfile.py` (que exercita as rotas autenticadas).

Uso:
    pip install httpx
    python tests/load/smoke_carga.py --url http://localhost:8080/health \
                                     --total 2000 --concorrencia 100
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def _worker(client, url, fila, latencias, erros):
    while True:
        try:
            fila.get_nowait()
        except asyncio.QueueEmpty:
            return
        t0 = time.perf_counter()
        try:
            resp = await client.get(url, timeout=30.0)
            latencias.append((time.perf_counter() - t0) * 1000)
            if resp.status_code >= 500:
                erros.append(resp.status_code)
        except Exception as exc:  # noqa: BLE001
            erros.append(type(exc).__name__)


async def rodar(url: str, total: int, concorrencia: int) -> int:
    fila: asyncio.Queue = asyncio.Queue()
    for _ in range(total):
        fila.put_nowait(1)
    latencias: list[float] = []
    erros: list = []

    limits = httpx.Limits(max_connections=concorrencia, max_keepalive_connections=concorrencia)
    async with httpx.AsyncClient(limits=limits) as client:
        inicio = time.perf_counter()
        await asyncio.gather(
            *[_worker(client, url, fila, latencias, erros) for _ in range(concorrencia)]
        )
        dur = time.perf_counter() - inicio

    ok = len(latencias)
    print(f"\nURL:            {url}")
    print(f"Requisições:    {total}  (concorrência {concorrencia})")
    print(f"Duração:        {dur:.2f}s  ->  {ok / dur:,.0f} req/s")
    print(f"Erros (5xx/exc):{len(erros)}  ({100 * len(erros) / max(total, 1):.2f}%)")
    if latencias:
        latencias.sort()
        p = lambda q: latencias[min(len(latencias) - 1, int(q * len(latencias)))]  # noqa: E731
        print(f"Latência ms:    média {statistics.mean(latencias):.0f} | "
              f"p50 {p(0.50):.0f} | p95 {p(0.95):.0f} | p99 {p(0.99):.0f} | max {latencias[-1]:.0f}")
    # Falha (exit!=0) se houve erro de servidor — útil em CI.
    return 1 if erros else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Teste bruto de concorrência.")
    ap.add_argument("--url", default="http://localhost:8080/health")
    ap.add_argument("--total", type=int, default=2000)
    ap.add_argument("--concorrencia", type=int, default=100)
    args = ap.parse_args()
    return asyncio.run(rodar(args.url, args.total, args.concorrencia))


if __name__ == "__main__":
    raise SystemExit(main())

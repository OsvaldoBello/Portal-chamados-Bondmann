# Benchmark do sistema — resultados

> Executado em **2026-07-04** contra o Supabase de produção (`iurlzlhbnoemkzgexcfk`,
> região us-east-2), medindo a **camada de banco sob RLS** — o gargalo real do sistema
> (a Seção 2 do plano mestre estima piso de ~320 ms/request só de latência de rede ao
> banco; o tempo de CPU das queries é ordens de grandeza menor).

## Metodologia

- **Volume sintético:** **6.000 chamados** (~2.000 por setor TI/RH/Marketing) +
  **12.000 mensagens**, semeados com `session_replication_role = replica` (triggers
  desligados → o contador `BOND-YYYY-NNNNN` de produção **não** avançou).
- **RLS real:** cada query rodou com `SET LOCAL ROLE authenticated` +
  `set_config('request.jwt.claims', …)` — o **mesmo mecanismo do app** (Seção 3.1).
- **Medição:** `EXPLAIN (ANALYZE, BUFFERS)` — tempo de execução no servidor.
- **Limpeza:** todos os dados sintéticos removidos ao final (voltou a 10 chamados /
  11 mensagens). Produção intacta.

## Resultados (a 6.010 chamados / 12.011 mensagens)

| Query quente | Papel (escopo RLS) | Linhas no escopo | Tempo de execução | Plano |
|---|---|---:|---:|---|
| **Fila do workspace** (lista/kanban) | RH | 2.007 de 6.010 | **8,8 ms** | Seq scan + hash joins, top-N sort |
| **KPIs do dashboard** (agregação: SLA, CSAT, TMA) | TI | 6.010 (tudo) | **4,1 ms** | Seq scan + aggregate |
| **Sino de notificações** | RH | 1.503 | **6,8 ms** | Seq scan + top-N |
| **Polling ETag da fila** (a cada 15 s) | RH | 2.007 | **3,2 ms** | Seq scan + count/max |
| **Chat — carregar mensagens** | RH | 2 msgs | **2,4 ms** | **Index Scan `idx_mensagens_chamado`** |

Planning time em todas: 0,9–3,5 ms.

## Leitura dos resultados

- **Tudo < 10 ms** a 6 mil chamados — 1–2 ordens de grandeza **abaixo** da latência de
  rede ao banco (~300 ms). Na prática, o tempo de resposta percebido é dominado pela
  rede, não pela query. Rodar o banco na mesma região da app (ou local) é o maior ganho.
- **RLS custa pouco:** os helpers (`auth.uid()`, `auth_is_ti()`, `auth_departamento_id()`)
  aparecem como **InitPlan avaliado 1×/query** (não por linha) — confirma a otimização
  `auth_rls_initplan` da migration `0014`.
- **Escopo por setor funciona:** RH filtra 2.007 de 6.010 (removeu 4.003); TI vê tudo.
- **Chat é index-backed** (`idx_mensagens_chamado`) → **não** degrada com o volume total
  de mensagens; fica ~2 ms independentemente do total.
- **Polling barato (3,2 ms) + ETag/304:** 100 usuários a cada 15 s ≈ 6,7 req/s na
  assinatura da fila — carga desprezível; e o `304 Not Modified` evita o re-render quando
  nada mudou (Seção 2.2).

## Meta de 100 CCU

Com esses números, 100 usuários simultâneos geram carga de banco trivial. Os limites
práticos ficam em **conexões do pooler** (Supavisor transaction mode, porta 6543 —
Seção 2.1) e no **Realtime** (sino/chat), não na CPU das queries. Mitigações já no lugar:
1 conexão RLS por request, GZip, cache TTL de catálogos, rate limit, ETag/304.

## Recomendação de escala (futuro)

As queries de **fila**, **KPIs** e **sino** fazem *seq scan* em `chamados` (o filtro RLS
por `OR` de setor/autor não é sargável por um único índice). A ~6 mil linhas isso é
3–8 ms; o custo cresce **linearmente**. Quando `chamados` passar de ~50–100 mil linhas
**ativas**, considerar:
- **Arquivar** chamados `RESOLVIDO` antigos (tabela/partição histórica) para manter a
  tabela quente pequena — a fila só olha os não-resolvidos.
- Índice parcial `WHERE status <> 'RESOLVIDO'` para a fila.
- Migrar cache/rate-limit para **Redis** se subir para **> 1 réplica** (Seção 2.3/2.4).

## Teste de carga de aplicação (ponta a ponta)

O benchmark acima é da camada de dados. Para exercitar a stack completa (FastAPI + RLS +
render + Realtime) sob concorrência, use os scripts em [`tests/load/`](../tests/load/):

- `locustfile.py` — 100 CCU logados nas rotas reais (dashboard, fila, sino, config
  Realtime); mede p95 e taxa de 304.
- `smoke_carga.py` — rajada bruta de concorrência com percentis (p50/p95/p99).

Rodar **contra um ambiente de teste**, nunca produção.

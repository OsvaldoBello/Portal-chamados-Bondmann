# ADR-0002 — Pooling em Supavisor transaction mode

**Status:** Aceito · **Data:** 2026-06-26 (plano mestre inicial) · **Ref.:** plano mestre Seção 2.1

## Contexto

Toda conexão da aplicação ao Postgres passa pelo Supavisor (pooler gerenciado
do Supabase), que oferece dois modos:

- **Transaction mode (porta `6543`):** devolve a conexão ao pool a cada
  transação — mais eficiente sob alta concorrência, mas quebra `SET` de
  sessão (sem `LOCAL`), prepared statements nomeados e qualquer contexto que
  precise viver além de uma transação.
- **Session mode (porta `5432`):** conexão dedicada por sessão — compatível
  com qualquer padrão de uso, mas não escala da mesma forma sob 100 CCU.

## Decisão

**Transaction mode (`6543`) para o pool de aplicação `asyncpg`.** Session mode
fica reservado só para tarefas administrativas/migrations que exijam contexto
de sessão persistente.

Essa escolha **é o motivo** por trás de ADR-0001 (RLS via `SET LOCAL` em vez de
`SET` de sessão) — não dá para confiar em estado de sessão sob esse pooler, e é
por isso que os claims de RLS são injetados por transação. `asyncpg` roda com
`statement_cache_size=0` (prepared statements nomeados desligados) para
conviver com o pooler nesse modo.

## Consequências

- Todo acesso ao domínio precisa reabrir/reinjetar contexto (role + claims) a
  cada transação — não há atalho de "configurar uma vez por conexão".
- O limite de conexões do plano Supabase e o dimensionamento de
  `min_size`/`max_size` do pool `asyncpg` precisam ficar abaixo do teto do
  Supavisor, considerando réplicas (ver checklist de scale-out, Seção 2.5 do
  plano mestre, Sprint 2 / item 2.9-B1).
- Task de manutenção que precise de `SET` de sessão persistente (não `LOCAL`)
  deve usar a porta de session mode explicitamente, não o pool padrão da app.

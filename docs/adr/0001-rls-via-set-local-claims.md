# ADR-0001 — Isolamento multi-tenant via RLS + `SET LOCAL` claims (não `service_role`)

**Status:** Aceito · **Data:** 2026-06-26 (plano mestre inicial) · **Ref.:** plano mestre Seção 3.1

## Contexto

O acesso ao domínio (chamados, mensagens, histórico) precisa impor isolamento
por RLS sem abrir mão de conexão via pool (Supavisor, ver ADR-0002). A
`service_role` key do Supabase **bypassa RLS por design** — usá-la sem cuidado
anula todo o isolamento entre setores/tenants.

Três opções avaliadas:

- **(A)** Propagar o JWT do usuário ao PostgREST/Supabase a cada request → RLS
  em vigor nativamente.
- **(B)** Acesso direto ao Postgres via `asyncpg`, injetando
  `SET LOCAL ROLE authenticated` + `set_config('request.jwt.claims', ...)` por
  transação, para o RLS enxergar o claim mesmo sob pooling.
- **(C)** `service_role` + filtro `empresa_id`/`departamento_id` obrigatório em
  100% das queries, com testes provando isolamento.

## Decisão

**(B) como caminho primário do domínio, (A) para Auth.** O domínio é acessado
via `asyncpg` conectado ao Supavisor em transaction mode; a cada transação,
antes de qualquer query, os claims do usuário autenticado são injetados
(`app/db.py::_apply_rls_claims`). Auth (login/refresh) usa `supabase-py` com a
anon key + JWT do usuário (opção A, natural para esse fluxo). A `service_role`
key **nunca** é usada para servir dado de usuário — só em tarefas
administrativas auditadas, nunca em rota acessível por request comum, e nunca
chega ao browser.

(C) foi **rejeitada como modelo primário**: depende de disciplina humana em
100% das queries, é frágil a longo prazo. Mantido apenas como defesa em
profundidade (filtro explícito de `empresa_id`/`departamento_id` mesmo com RLS
ativo).

## Consequências

- A escolha de (B) **condiciona** o modo de pooling (ADR-0002): só funciona
  com `SET LOCAL` (escopo transacional), não `SET` de sessão — porque o
  Supavisor em transaction mode devolve a conexão ao pool a cada transação.
- `statement_cache_size=0` obrigatório no `asyncpg` (prepared statements
  nomeados não sobrevivem entre transações sob esse pooler).
- Os claims são embutidos como literal SQL manualmente escapado (não há bind
  de parâmetro para múltiplos comandos SQL numa simple query) — a
  correção desse escape é validada por teste adversarial dedicado
  (`tests/test_db_rls_claims.py`, Sprint 2 / item 2.9-B5).
- Toda a suíte e2e de RLS (`tests/e2e/`, Sprint 1 / item 1.7) reusa esse mesmo
  helper de produção (`_apply_rls_claims`) para trocar de persona nos testes,
  em vez de reimplementar a técnica à parte — garante que o teste valida o
  mecanismo real, não uma cópia que pode divergir.

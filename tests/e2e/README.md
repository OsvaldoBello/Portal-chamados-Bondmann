# Suíte e2e de RLS (item 1.7 / M9)

Testa a matriz de visibilidade de RLS contra um Postgres real (Supabase local),
não contra `FakeRepo`/mocks — cobre a classe de bug "mock verde × banco real
divergente" que motivou este item (caso 0028/`chamados_departamento`).

## Rodar localmente

Pré-requisitos: Docker rodando + [Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started).

```bash
supabase start          # sobe Postgres local e aplica supabase/migrations/*.sql
export RLS_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres
python -m pytest tests/e2e -m rls -v
supabase stop
```

Sem `RLS_DATABASE_URL` configurada, estes testes são **pulados automaticamente**
(ver `pytest_collection_modifyitems` em `conftest.py`) — o `pytest` default do
resto do projeto (`python -m pytest`) continua verde sem precisar de Docker.

## CI

`.github/workflows/e2e-rls.yml` roda esta suíte num job separado, só quando o PR
toca `supabase/migrations/`, `app/repositories/`, `app/db.py` ou `tests/e2e/`.

## Como os testes funcionam

Cada teste abre **uma** conexão/transação (`conn`) que nunca comita — o fixture
`seed` popula departamentos/usuários/chamados/mensagens de teste nela (como
superusuário, sem RLS), e `as_user(conn, user_id)` troca a conexão para uma
persona simulada exatamente como `app/db.py::_apply_rls_claims` faz em produção
(`SET LOCAL ROLE authenticated` + `set_config('request.jwt.claims', ...)`).  O
`ROLLBACK` no teardown do `conn` já limpa tudo — sem truncar tabela nenhuma entre
testes.

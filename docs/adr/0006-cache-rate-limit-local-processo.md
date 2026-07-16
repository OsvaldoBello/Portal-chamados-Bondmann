# ADR-0006 — Cache e rate limit local-por-processo (gatilho de migração pra Redis)

**Status:** Aceito · **Data:** 2026-06-26 (plano mestre inicial) · **Ref.:** plano mestre Seções 2.3/2.4/2.5

## Contexto

Categorias e planos de SLA são lidos com frequência e escritos raramente —
bons candidatos a cache. O rate limiter (`slowapi`, em `/login` e abertura de
chamado) também precisa de algum storage de contagem. Railway pode rodar
múltiplas réplicas no futuro, o que quebra qualquer storage local-por-processo
(cada réplica teria seu próprio estado, divergente das outras).

## Decisão

**MVP com storage local-por-processo em ambos:**

- Cache: dicionário em memória (`app/cache.py`), TTL curto (60–120s), chave
  **sempre** incluindo o escopo do tenant/departamento — nunca uma chave
  global sem escopo (vazamento cross-tenant seria um risco Crítico).
  Invalidado na escrita (admin cria/edita categoria ou plano).
- Rate limit: storage in-memory padrão do `slowapi`, por réplica.

**Gatilho explícito de migração para Redis:** se o Railway rodar **mais de 1
réplica** com requisito de invalidação imediata e consistente, migrar os dois
(cache e rate limiter) para Redis compartilhado — sem isso, o limite efetivo
do rate limiter é multiplicado pelo número de réplicas, e o cache diverge
entre réplicas.

## Consequências

- Simplicidade e zero dependência externa enquanto o deploy for 1 réplica
  (é o caso hoje, Railway/ADR-0005).
- **Não bloqueante para go-live com 1 réplica** — está registrado como item
  de evolução, não pendência.
- O checklist de scale-out (plano mestre Seção 2.5, Sprint 2 / item 2.9-B1)
  consolida os pontos concretos a mudar quando essa decisão for revisitada
  (cache, rate limiter, dimensionamento do pool `asyncpg`) — antes de subir
  réplicas > 1, os itens desse checklist precisam estar resolvidos.

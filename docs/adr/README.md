# ADRs — Portal de Chamados Bondmann

Registro das decisões arquiteturais **grandes** do projeto (Sprint 2 / item 2.7,
B4) — não é para toda mudança de feature, que continua indo só pro
[`docs/CHANGELOG.md`](../CHANGELOG.md). Um ADR entra aqui quando muda algo
estrutural difícil de reverter: modelo de isolamento de dados, topologia de
deploy, estratégia de pooling, pivô de produto.

Formato: `NNNN-titulo-curto.md`, numeração sequencial, nunca reescrita — um ADR
superado por decisão posterior fica marcado `Status: Substituído por ADR-000X`,
não é apagado (histórico do "porquê" importa tanto quanto o estado atual).

| ADR | Título | Status |
|---|---|---|
| [0001](0001-rls-via-set-local-claims.md) | Isolamento multi-tenant via RLS + `SET LOCAL` claims (não `service_role`) | Aceito |
| [0002](0002-pooling-supavisor-transaction-mode.md) | Pooling em Supavisor transaction mode | Aceito |
| [0003](0003-pivo-sistema-interno-departamento.md) | Pivô: sistema interno com roteamento por departamento | Aceito |
| [0004](0004-sla-horario-comercial.md) | SLA em horário comercial (não horas corridas) | Aceito |
| [0005](0005-railway-alvo-unico-deploy.md) | Railway como alvo único de deploy | Aceito |
| [0006](0006-cache-rate-limit-local-processo.md) | Cache e rate limit local-por-processo (gatilho de migração pra Redis) | Aceito |
| [0007](0007-mfa-totp-aal2-admin.md) | MFA (TOTP) com enforcement de `aal2` para ADMIN | Aceito |

Cada ADR referencia a seção correspondente do
[`plano_mestre_desenvolvimento.md`](../../plano_mestre_desenvolvimento.md),
que continua sendo a fonte de verdade operacional (schema, RLS, cronograma) —
o ADR registra **por que** a decisão foi tomada e quais alternativas foram
descartadas, não os detalhes de implementação.

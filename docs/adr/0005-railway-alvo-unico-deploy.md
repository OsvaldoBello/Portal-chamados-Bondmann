# ADR-0005 — Railway como alvo único de deploy

**Status:** Aceito · **Data:** 2026-07-15 · **Ref.:** plano de melhorias, item 1.8 (M10); plano mestre, tabela de Estado

## Contexto

O projeto rodou em dois alvos de deploy em paralelo por um período
(2026-07-01 a 2026-07-15): **Vercel** (serverless, `@vercel/python`) e
**Railway** (Dockerfile, processo persistente). Essa duplicação criava ramos
condicionais no código (`Settings.is_serverless`) para acomodar as
diferenças: pool `asyncpg` efêmero (`min_size=0`) no modo serverless, e envio
de e-mail inline em vez de `BackgroundTasks` (que morre pós-response numa
function serverless, sem garantia de conclusão).

Duas opções avaliadas (auditoria, item M10):

- **(a) Railway como produção única** — processo persistente, `libmagic` real
  via apt, `BackgroundTasks` com garantia de conclusão.
- **(b) manter Vercel** — exigiria implementar outbox/fila com retry para
  e-mails, já que `BackgroundTasks` não é confiável em serverless.

## Decisão

**(a) Railway única.** Decisão do gestor (Osvaldo, 2026-07-15). Vercel
desativado por completo: `vercel.json`/`.vercelignore` removidos,
`[tool.vercel]` tirado do `pyproject.toml`, `Settings.is_serverless`
eliminado, e os ramos condicionais que ele acionava em
`app/db.py::init_pool` e `app/notification.py::agendar_notificacao_email`
removidos — o caminho de servidor persistente (pool completo,
`BackgroundTasks` sempre assíncrono) passa a ser o único caminho.

## Consequências

- Simplifica `app/config.py`, `app/db.py`, `app/notification.py` — sem mais
  bifurcação por ambiente de deploy.
- Notificação por e-mail ganha garantia de entrega (não morre pós-response).
- Destrava o item 2.5 (fonte única de dependências, `requirements.txt` vs.
  `pyproject.toml`) — sem Vercel, não há mais motivo para manter
  `pyproject.toml [project.dependencies]` como espelho "para o caso de"
  alguém rodar `@vercel/python` a partir dele.
- Qualquer decisão futura de reintroduzir um segundo alvo de deploy precisa
  reavaliar esta ADR, não só adicionar um novo `if is_x`.

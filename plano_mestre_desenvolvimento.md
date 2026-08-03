# Plano Mestre de Desenvolvimento — Portal de Chamados Bondmann Química

> **Documento vivo.** Esta é a fonte de contexto persistente para todo o desenvolvimento do Portal de Chamados Bondmann Química (Help Desk / Service Desk multi-tenant). Deve ser revisado e atualizado ao final de cada PR, feature ou correção complexa (ver Seção 7).
>
> **Fonte de verdade do produto:** `plano_projeto_portal_chamados_v2.docx` (v2.0, Junho/2026). Este MD **consolida e detalha** a spec — não a substitui nem reinventa. Toda decisão de implementação não coberta pela spec está marcada como `[DECISÃO DE ENGENHARIA]`. Toda afirmação não verificável está marcada como `⚠️ SUPOSIÇÃO A VALIDAR`, e versões incertas como `⚠️ VERSÃO A CONFIRMAR`.

---

## Sumário

- [Seção 0 — Convenções, Versões e Stack Fixada](#seção-0--convenções-versões-e-stack-fixada)
- [Seção 0.1 — Contradições Resolvidas (obrigatório)](#seção-01--contradições-resolvidas-obrigatório)
- [Seção 1 — Visão Arquitetural e Stack](#seção-1--visão-arquitetural-e-stack)
- [Seção 2 — Escalabilidade (100 CCU) e Performance](#seção-2--escalabilidade-100-ccu-e-performance)
- [Seção 3 — Segurança e Isolamento Multi-Tenant](#seção-3--segurança-e-isolamento-multi-tenant)
- [Seção 4 — Testes e QA](#seção-4--testes-e-qa)
- [Seção 5 — Lógicas de Negócio Críticas (Schema Canônico)](#seção-5--lógicas-de-negócio-críticas-schema-canônico)
- [Seção 6 — Fluxo de Desenvolvimento e Cronograma Modular](#seção-6--fluxo-de-desenvolvimento-e-cronograma-modular)
- [Seção 7 — Protocolo de Atualização de Contexto (doc vivo)](#seção-7--protocolo-de-atualização-de-contexto-doc-vivo)
- [Changelog](#changelog)
- [Tabela de Estado de Implementação](#tabela-de-estado-de-implementação)

---

## Seção 0 — Convenções, Versões e Stack Fixada

### 0.1 Regra de versionamento

Toda biblioteca usada no projeto **deve** ter versão-alvo fixada em `requirements.txt`/`pyproject.toml` (pinned, com hash quando possível) e CDN/CLI fixados por versão exata no frontend. Versões abaixo refletem o estado estável conhecido até **Janeiro/2026**; as marcadas `⚠️ VERSÃO A CONFIRMAR` devem ser checadas no PyPI/registro oficial no momento do `setup` (Fase 1) e travadas no lockfile **antes** de qualquer outro trabalho.

### 0.2 Backend (Python)

| Componente | Versão-alvo | Observação |
|---|---|---|
| Python | `3.12.x` | Runtime. `[DECISÃO DE ENGENHARIA]` 3.12 por estabilidade do ecossistema async e suporte amplo em imagens Docker slim. Evitar 3.13 até confirmar suporte de todas as libs nativas (`asyncpg`, `python-magic`). |
| FastAPI | `fastapi==0.115.*` | `⚠️ VERSÃO A CONFIRMAR` — travar patch exato no setup. |
| Uvicorn | `uvicorn[standard]==0.34.*` | Servidor ASGI. Porta **8080** (exigência Railway). `⚠️ VERSÃO A CONFIRMAR`. |
| supabase-py | `supabase==2.*` | Client oficial. Usar **`create_async_client`** (AsyncClient). `⚠️ VERSÃO A CONFIRMAR` o patch e a superfície async (ver Seção 1.4). |
| Jinja2 | `jinja2==3.1.*` | Templating server-side. Autoescape **sempre ligado** (ver Seção 3.10). |
| Pydantic | `pydantic==2.*` | Validação de input. |
| Pydantic Settings | `pydantic-settings==2.*` | Carregamento de config/secrets. |
| asyncpg | `asyncpg==0.30.*` | Acesso direto ao Postgres (path de RLS via `SET LOCAL`, ver Seção 3.1). `⚠️ VERSÃO A CONFIRMAR`. |
| SQLAlchemy | `sqlalchemy[asyncio]==2.0.*` | `[DECISÃO DE ENGENHARIA]` opcional, apenas se quisermos query builder tipado sobre asyncpg. Para o MVP, asyncpg cru + SQL parametrizado é suficiente. |
| slowapi | `slowapi==0.1.*` | Rate limiting. `⚠️ VERSÃO A CONFIRMAR`. |
| python-multipart | `python-multipart==0.0.*` | Necessário para upload de arquivos no FastAPI. `⚠️ VERSÃO A CONFIRMAR`. |
| python-magic | `python-magic==0.4.*` | Validação de MIME por *magic bytes* (Seção 3.9). Requer `libmagic` na imagem Docker. |
| itsdangerous | `itsdangerous==2.*` | Assinatura de token CSRF e/ou cookie de sessão. |
| httpx | `httpx==0.27.*` | Cliente HTTP (testes e chamadas REST eventuais). `⚠️ VERSÃO A CONFIRMAR`. |
| PyJWT | `pyjwt[crypto]==2.10.1` | `[DECISÃO DE ENGENHARIA]` adicionado ao stack (não estava na spec): verificação **local** de JWT por request (JWKS RS256/ES256 + fallback HS256, Seção 3.6). Validar via API do Supabase a cada request seria gargalo. Travado no lockfile. |

### 0.3 Testes

| Componente | Versão-alvo | Observação |
|---|---|---|
| pytest | `pytest==8.*` | |
| pytest-asyncio | `pytest-asyncio==0.24.*` | `⚠️ VERSÃO A CONFIRMAR`. Modo `asyncio_mode = auto`. |
| Supabase CLI | `supabase` (CLI) `≥ 1.x` | Para `supabase start` (stack local) e `supabase/migrations`. `⚠️ VERSÃO A CONFIRMAR`. |

### 0.4 Frontend (decisões de versão críticas — quebram código)

| Componente | Versão-alvo | Decisão |
|---|---|---|
| **HTMX** | **`htmx 2.0.x`** | `[DECISÃO DE ENGENHARIA]` — projeto novo, sem legado: adotar **HTMX 2.0**. Diferenças que quebram vs 1.x: em 2.0 o atributo `hx-on` mudou de sintaxe (`hx-on:click` em vez de `hx-on="click: ..."`); `withCredentials`/CORS e o tratamento de `HX-Trigger` foram revisados; extensões agora vêm em pacotes separados (ex.: `htmx-ext-sse`, `htmx-ext-ws`). **Toda referência a HTMX neste doc assume 2.0.** Fixar a versão exata do bundle via CDN com SRI (Seção 3.8). |
| **Tailwind CSS** | **`tailwindcss 3.4.x` (build via CLI)** | `[DECISÃO DE ENGENHARIA]` — adotar **Tailwind v3.4** por estabilidade e configuração madura (`tailwind.config.js` + `content` purge). **NÃO** usar v4 nesta fase: o v4 troca a configuração JS por config CSS-first (`@theme`, `@import "tailwindcss"`) e muda o engine — incompatível com a base de conhecimento e com plugins atuais. Migração para v4 fica no backlog. Ver contradição resolvida em 0.1. `⚠️ VERSÃO A CONFIRMAR` o patch. |
| **Alpine.js** | **`alpinejs 3.x` — CSP build** | `[DECISÃO DE ENGENHARIA]` — usar o **Alpine CSP build** (e não o build padrão), porque a CSP de produção **não** terá `unsafe-eval` (Seção 3.8). O CSP build restringe a sintaxe de expressões: **não** aceita expressões JS arbitrárias inline; só nomes de propriedades/métodos definidos no componente. Todo código Alpine deve respeitar essa restrição desde o início (sem `x-data="{ ... lógica complexa ... }"` com expressões; usar métodos nomeados). |
| Sortable.js | `sortablejs 1.15.x` | Apenas para drag-and-drop do Kanban (mitigação de risco da spec, Seção 7 do .docx). HTMX persiste a mudança. `⚠️ VERSÃO A CONFIRMAR`. |
| Chart.js | `chart.js 4.x` | Gráficos do painel admin. Alternativa ApexCharts (spec cita ambas); `[DECISÃO DE ENGENHARIA]`: **Chart.js 4** por leveza e licença. |

> `⚠️ SUPOSIÇÃO A VALIDAR:` as versões de CDN do HTMX/Alpine/Sortable/Chart serão servidas **self-hosted** a partir de `/static` em produção (não de CDN público), para permitir CSP estrita com `SRI` e evitar dependência de terceiros. Em dev pode-se usar CDN. Validar no setup (Fase 1).

---

## Seção 0.1 — Contradições Resolvidas (obrigatório)

A spec e o material de briefing divergem nos pontos abaixo. **Cada um tem uma única decisão cravada:**

### C1 — Fallback de SLA

- **Conflito:** briefing diz "fallback global de 4h"; spec (`calcular_sla_chamado`) diz "12h/24h".
- **DECISÃO (escada de fallback coerente):** o cálculo de prazo resolve nesta ordem, para cada métrica (resposta e resolução):
  1. **URGENTE** = **50% do tempo de ALTA** do plano da empresa (regra da spec, Seção 4.2).
  2. Se a prioridade existe no plano da empresa → usa o valor do plano.
  3. Se **ALTA inexistente** (necessária para derivar URGENTE) ou a prioridade pedida não está configurada → usa o **default do plano** (`[DECISÃO]` coluna de default por plano; ver schema).
  4. Se o **plano não está configurado** para a empresa → **fallback global**: **resposta = 12h, resolução = 24h** (valores literais da spec).
- **Status:** `⚠️ VALIDAR COM O GESTOR` — a própria spec (Seção 8.1 do .docx) exige validar a lógica de SLA urgente com o gestor **antes da Fase 4**. O número global de 4h do briefing foi **descartado** em favor de 12h/24h da spec (fonte de verdade).

### C2 — Signed URLs de anexos

- **Conflito:** briefing pede "cache para não expirar na sessão"; spec define "expiração de 1h".
- **DECISÃO:** **Signed URL com TTL de 1 hora (3600s)**, **regenerada a cada renderização** do anexo no backend (geração *on-demand*). **Proibido** cachear a URL assinada além do TTL (em sessão, cookie, Alpine state ou memória de aplicação). Padrão: o template recebe a URL assinada já no fragmento HTML servido; ao recarregar o fragmento (HTMX), gera-se nova URL. Ver Seção 3.9.

### C3 — Tailwind

- **Conflito:** briefing diz "sem CSS externo"; spec diz "via CDN".
- **DECISÃO:** **Build step com Tailwind CLI no Dockerfile** — CSS compilado e *purgado* (`content` apontando para templates Jinja) e servido como asset estático versionado. Interpretação de "sem CSS externo": **sem CSS customizado escrito à mão**, apenas utilitários Tailwind. **CDN do Tailwind só em desenvolvimento, nunca em produção** (o CDN runtime do Tailwind é incompatível com CSP estrita e com o purge). Ver Seção 0.4 e Seção 6 (Dockerfile).

---

## Seção 1 — Visão Arquitetural e Stack

### 1.1 Padrão geral

Aplicação **server-rendered** (SSR) com reatividade por **fragmentos HTML**: o FastAPI é a **fonte de verdade**; o navegador apenas exibe e dispara requisições. Fluxo (consolidado do diagrama da spec, Seção 3.3):

```
Browser (HTML5 + Tailwind + Alpine.js CSP)
   │  HTMX dispara requisição parcial (hx-get/post/...)
   ▼
FastAPI (roteamento, lógica, auth, background tasks)
   │  await (asyncpg / supabase-py async)
   ▼
Supabase: PostgreSQL (+ RLS) · Storage (privado) · Realtime (chat)
   │  retorna dados
   ▼
FastAPI renderiza fragmento Jinja2  ──►  HTMX faz swap parcial no DOM
```

### 1.2 Responsabilidades (fronteiras rígidas)

| Camada | Responsabilidade | NÃO faz |
|---|---|---|
| **FastAPI** | Roteamento, lógica de negócio, autenticação/sessão, validação Pydantic, background tasks (e-mail, webhooks), geração de signed URLs. | Não delega regra de negócio ao cliente. |
| **Jinja2 + HTMX** | Renderização SSR e reatividade via fragmentos HTML. HTMX faz AJAX parcial e troca de DOM. | HTMX não guarda estado de domínio. |
| **Alpine.js (CSP build)** | Interatividade **estrita de UI**. | Não guarda dados de negócio (ver 1.3). |
| **Tailwind** | Estilização por utilitários (build CLI). | Sem CSS customizado à mão. |

### 1.3 Fronteira do Alpine.js (exemplos concretos)

**PERMITIDO (estado efêmero de UI):**
- Abrir/fechar **modal**.
- Abrir/fechar **dropdown**.
- **Toggle de aba** / accordion.
- Alternância **Lista ↔ Kanban** (preferência visual da tela do operador).
- **Fundo amarelo** da caixa quando "Nota Interna" está ativa (toggle visual).

**PROIBIDO (pertence ao backend):**
- Guardar a **lista de chamados** ou qualquer coleção de domínio.
- Guardar **dados de formulário que são fonte de verdade** (o form posta para o FastAPI; a verdade é o banco).
- Qualquer estado que represente **persistência** (status do chamado, conteúdo da mensagem após envio, flag `is_interna` salva). O toggle visual da nota é UI; o valor salvo é decidido e validado no servidor.

### 1.4 supabase-py assíncrono e fallback de async incompleto

- Usar **`create_async_client(url, key)`** → `AsyncClient`, com **`await`** em todas as chamadas de dados:
  ```
  client = await create_async_client(SUPABASE_URL, SUPABASE_KEY)
  res = await client.table("chamados").select("*").eq("empresa_id", emp).execute()
  ```
- `⚠️ SUPOSIÇÃO A VALIDAR:` a superfície **async de Storage e Realtime** do `supabase-py` historicamente é **menos completa** que a de `table()/auth()`. A cobertura exata deve ser verificada na versão travada no setup.
- **`[LACUNA — DECIDIR]` Fallback onde o async do SDK for incompleto:**
  - **Operações de Storage** (gerar signed URL, upload): se a API async cobrir, usar `await`; **senão**, rodar a chamada síncrona em threadpool via **`anyio.to_thread.run_sync(...)`** para não bloquear o event loop. **Decisão:** preferir `anyio.to_thread` para Storage — é I/O pontual e de baixa frequência, não justifica reimplementar contra a API S3S do Supabase.
  - **Operações pesadas/quentes de banco** (fila de chamados, dashboards, qualquer query com filtro de tenant e RLS): **ir direto ao Postgres via `asyncpg`** (com `SET LOCAL` de claims — Seção 3.1), **não** via PostgREST/supabase-py. **Justificativa:** (a) controle total sobre a transação para aplicar RLS sob pooling; (b) menor overhead que a camada REST; (c) SQL parametrizado e índices explícitos. O supabase-py fica para **Auth** (login/refresh/signup) e **Storage**.
  - **Realtime:** ver decisão de topologia na Seção 6 (browser conecta direto via `supabase-js`, não pelo SDK Python).

> **Resumo da divisão de acesso a dados:** `asyncpg` (RLS via `SET LOCAL`) para leitura/escrita de domínio · `supabase-py` async para Auth · `supabase-py` (async ou `anyio.to_thread`) para Storage · `supabase-js` no browser para Realtime do chat.

---

## Seção 2 — Escalabilidade (100 CCU) e Performance

Alvo: **100 usuários simultâneos** confortavelmente.

### 2.1 Connection Pooling (Supavisor) — obrigatório

- Toda conexão ao Postgres passa pelo **Supavisor** (pooler gerenciado do Supabase).
- **`[LACUNA — DECIDIR]` modo e porta:**
  - **Transaction mode — porta `6543`** (DECISÃO para o pool de aplicação `asyncpg`).
  - **Session mode — porta `5432`** reservado apenas para tarefas administrativas/migrations que exijam contexto de sessão.
- **Justificativa e interação com RLS:** transaction mode **devolve a conexão ao pool a cada transação**, o que **quebra**: `SET` de sessão (sem `LOCAL`), *prepared statements* nomeados e qualquer contexto que dependa de viver além da transação. Isso **condiciona** a estratégia de RLS da Seção 3: como não podemos confiar em `SET` de sessão persistente, **injetamos os claims por transação com `SET LOCAL`** (escopo transacional) — compatível com transaction mode. Configurar `asyncpg` com `statement_cache_size=0` (prepared statements desligados) para conviver com o pooler em transaction mode. `⚠️ VALIDAR` o limite de conexões do plano pago e dimensionar `min_size/max_size` do pool abaixo desse teto, considerando réplicas.

### 2.2 Conta de carga (polling)

- Polling de **5s × 100 CCU ≈ 20 req/s** apenas na fila. Mitigações obrigatórias:
  - **Polling só com a aba visível** — usar `hx-trigger` condicionado à visibilidade (Page Visibility API via Alpine para pausar/retomar; em HTMX, `every 5s` combinado com pausa quando `document.hidden`).
  - **ETag / `304 Not Modified`** nas rotas de fila: o backend calcula um ETag (ex.: hash de `max(updated_at)` + contagem do conjunto filtrado por tenant); se inalterado, responde `304` sem re-renderizar.
  - Alternativa/complemento: **`HX-Trigger`** para mandar o cliente recarregar apenas quando há mudança real, evitando swap desnecessário.
  - **Realtime no chat** elimina polling da tela de atendimento individual (ver Seção 6).
- **Limites do plano Supabase pago:** monitorar desde a Fase 1 (dashboard). Documentar teto de conexões do pooler e de Realtime; dimensionar pool e réplicas dentro do teto.

### 2.3 Cache em memória — tenant-scoped

- **PROIBIDO** `functools.lru_cache` em função **async** (ela cacheia a *coroutine*, não o resultado — bug silencioso).
- Itens cacheáveis: **Categorias** e **Planos de SLA** (leitura frequente, escrita rara).
- **Chave de cache DEVE incluir `empresa_id`** (tenant-scoped). Estrutura: `cache[(empresa_id, "categorias")] = (valor, expira_em)`.
  - **`⚠️ ALERTA DE SEGURANÇA:`** se a chave **não** incluir o tenant, há **vazamento cross-tenant** (empresa A vê categorias/planos de B). Isso é um vetor do risco "Crítico" da spec. Toda função de cache deve receber `empresa_id` explicitamente.
- **TTL curto** (ex.: 60–120s) + **invalidação na escrita** (ao criar/editar categoria ou plano, expurgar a chave do tenant).
- **`[LACUNA — DECIDIR]` topologia (Railway pode ter múltiplas réplicas):**
  - **DECISÃO para o MVP:** **cache local por-processo, TTL curto** (implementação simples, sem dependência externa). Aceita-se *staleness* de até o TTL e divergência momentânea entre réplicas — tolerável para dados quase-estáticos (categorias/planos).
  - **Gatilho de migração para Redis compartilhado:** se rodarmos **> 1 réplica** com requisito de invalidação imediata e consistente, mover o cache (e o storage do rate limiter — 2.4) para **Redis**. Registrar como item de evolução, não bloqueante para go-live com 1 réplica.

### 2.4 Rate limiting (slowapi)

- **`slowapi`** em **`/login`** e na **abertura de chamado** (POST). Demais mutações sensíveis conforme necessário.
- **IP real atrás do proxy Railway:** extrair via **`X-Forwarded-For`**, usando o **último** IP da cadeia (o único hop confiável, acrescentado pelo próprio Railway) — não o primeiro, que é escrito pelo cliente e portanto forjável (Sprint 1 / item 1.1, A5). `ProxyHeaders`/`--proxy-headers` no Uvicorn com `forwarded_allow_ips` apropriado. Não usar o IP da conexão direta (será o do proxy).
- **Isenção da rota de polling** da fila (senão usuários legítimos são bloqueados pelos ~20 req/s agregados).
- **`[LACUNA — DECIDIR]` storage do limiter:**
  - **DECISÃO MVP:** **in-memory por réplica** (default do slowapi) enquanto rodarmos **1 réplica**.
  - **Com múltiplas réplicas:** migrar para **Redis** (storage compartilhado) — caso contrário o limite efetivo é multiplicado pelo nº de réplicas. Mesmo gatilho do cache (2.3).

### 2.5 Checklist de scale-out (Sprint 2 / item 2.9-B1)

> Item de **infra**, não de código — nada aqui é acionado automaticamente; é o
> que precisa ser feito manualmente **antes** de subir réplicas > 1 no Railway.
> Hoje (1 réplica) nenhum destes pontos bloqueia produção.

- [ ] **Cache de catálogos (2.3):** trocar o cache local por-processo por **Redis**
      compartilhado — sem isso, cada réplica tem seu próprio TTL/estado e a
      invalidação na escrita do admin só limpa a réplica que atendeu o request.
- [ ] **Rate limiter (2.4):** trocar o storage in-memory do `slowapi` por **Redis**
      — sem isso, o limite efetivo (`5/min`, `15/min`) é multiplicado pelo nº de
      réplicas (cada uma conta à parte).
- [ ] **Pool de conexões (2.1):** redimensionar `min_size`/`max_size` do `asyncpg`
      por réplica para que a soma continue abaixo do teto do plano Supavisor
      (hoje dimensionado assumindo 1 réplica).
- [ ] Confirmar que nada mais assume estado em memória de processo único (ex.:
      contextvar de conexão RLS por request já é per-request, não precisa mudar).
- **Aceite deste item:** checklist registrado e revisitável — não é para ser
  executado agora, só existir pronto para quando a decisão de escalar horizontalmente
  for tomada.

---

## Seção 3 — Segurança e Isolamento Multi-Tenant

> **Seção mais crítica do projeto.** A spec classifica "vazamento entre tenants por RLS incorreto" como risco **Crítico**.

### 3.1 RLS × service_role — decisão central

A **`service_role` key bypassa o RLS** por design. Usá-la sem cuidado anula todo o multi-tenancy. Opções avaliadas:

- **(A)** Propagar o **JWT do usuário** ao PostgREST/Supabase a cada request → RLS em vigor nativamente.
- **(B)** **Acesso direto ao Postgres** com `set_config('request.jwt.claims', ...)` / **`SET LOCAL`** por transação, para o RLS enxergar o claim **sob pooling**.
- **(C)** `service_role` + filtro `empresa_id` obrigatório em 100% das queries, com testes provando isolamento.

**DECISÃO: (B) como caminho primário do domínio, (A) para Auth.**

- O domínio (fila, chamados, mensagens, histórico) é acessado via **`asyncpg`** conectado ao Supavisor **transaction mode** (Seção 2.1). Em **cada transação**, antes das queries, injetamos os claims do usuário autenticado:
  ```sql
  SET LOCAL ROLE authenticated;
  SELECT set_config('request.jwt.claims', $1::text, true);  -- true = LOCAL (escopo transação)
  ```
  Assim as políticas RLS que leem `auth.uid()` / `auth.jwt()` / claims funcionam **mesmo sob pooling**, porque `SET LOCAL` vive só na transação e não vaza para a próxima conexão emprestada do pool.
- **Auth** (login/refresh/signup) usa **supabase-py** com a **anon key** + JWT do usuário (opção A naturalmente).
- A **`service_role` key NUNCA** é usada para servir dados de usuário. Seu uso fica restrito a tarefas administrativas explícitas e auditadas (ex.: jobs de manutenção), **nunca** em rota acessível por request de usuário, e **jamais** chega ao browser (Seção 6 / config & secrets).
- **(C) é rejeitada como modelo primário** (depende de disciplina humana em 100% das queries — frágil). Mantemos, porém, o **filtro explícito de `empresa_id` como defesa em profundidade** mesmo com RLS ativo.
- **Ligação com pooling (Seção 2.1):** a escolha (B) é o que **permite** usar transaction mode com segurança — é a razão de `SET LOCAL` (e não `SET`) e de `statement_cache_size=0`.

> `⚠️ SUPOSIÇÃO A VALIDAR:` o nome do role do PostgREST/Supabase para usuários autenticados é `authenticated` e o claim lido pelas políticas é `request.jwt.claims`. Confirmar contra a versão do Supabase no setup; ajustar as funções `auth.uid()`/`auth.role()` conforme o schema `auth` provisionado.

### 3.2 Enum de papéis e matriz de permissões

Enum de papéis (coerente com a spec): **`ADMIN`**, **`OPERADOR`**, **`CLIENTE`**.

> ### 🔁 `[DECISÃO DE PRODUTO 2026-07-01]` Sistema INTERNO com roteamento por DEPARTAMENTO
>
> O portal é de **uso interno** da Bondmann Química (ninguém compra/contrata; não há
> multi-tenant de clientes externos). A dimensão de isolamento deixa de ser a
> **empresa** e passa a ser o **departamento** que atende o chamado:
>
> - **Departamentos** (`TI`, `RH`, `Marketing`) são uma **tabela gerenciável** (`departamentos`),
>   igual a categorias. Um funcionário abre chamado escolhendo o **departamento de destino**.
> - **Staff** (OPERADOR/ADMIN) tem um `departamento_id`; **só vê/atende os chamados do seu setor**.
> - **`[REFINADO 0010]` Acesso total = staff no departamento `TI`** (`auth_is_ti()`): TI vê/atende
>   **todos** os chamados e gere os catálogos. (Substitui o conceito anterior de "super-admin = ADMIN
>   com `departamento_id` NULO" da `0008`.)
> - **RH / Marketing** (staff): veem os chamados do **seu** departamento **+ os que eles mesmos abriram**;
>   podem **abrir para qualquer** departamento.
> - **Autor** (funcionário, CLIENTE, sem departamento) vê **apenas os chamados que abriu**.
> - **`empresas`/`planos_sla`** permanecem **apenas como configuração interna de SLA** (org única
>   = Bondmann); sem telas de venda/gestão de empresa nem de planos. `empresa_id` vira plumbing
>   interno (uma org), mantido para o path do Storage e o motor de SLA.
> - **Cadastro é direto no Supabase** (Authentication > Users) — **sem signup público**. O trigger
>   `handle_new_user` cria o perfil CLIENTE vinculado à org interna; a promoção de papel/departamento
>   é feita por SQL (`supabase/registro_usuarios.sql`). Ver Seção 3.4.
>
> Materializado na migration `0008_departamentos_roteamento` (+ `0009` de hardening). As linhas
> abaixo marcadas com 🔁 refletem o novo modelo.
>
> ### 🔁 `[DECISÃO DE PRODUTO 2026-07-06]` `0020` — TI deixa de ter visibilidade/atendimento total
>
> A tabela/texto acima (e a matriz logo abaixo) refletem o modelo da `0010`. A migration
> `0020_ti_escopo_departamento` **reverteu só o ramo de visibilidade/atendimento** de `auth_is_ti()`:
> o staff de TI passa a **ver e atender apenas os chamados do próprio departamento (TI)**, como
> qualquer outro setor — **não mais "acesso total"** de leitura/escrita. TI **mantém** os poderes de
> administrador (gestão de catálogos/usuários/planos) e o **repasse** de chamados entre setores
> (grava um `departamento_id` fora do setor atual via `WITH CHECK auth_is_ti()` — mas só em
> chamados que **já estão** na fila do TI, o `USING` da policy de `UPDATE` não abre exceção). Este
> MD nunca tinha sido atualizado para essa mudança; documentado agora porque a `0028` (abaixo)
> constrói em cima dela — um bug corrigido nesta sessão (ver changelog 2026-07-08) veio exatamente
> de assumir "TI = acesso total" na tela de atendimento, quando isso já não era mais verdade desde a `0020`.
>
> ### 🔁 `[DECISÃO DE PRODUTO 2026-07-08]` `0027`/`0028` — Setor unificado com Departamento + Líder de setor
>
> "Setor" (a área da empresa a que um usuário pertence — Comercial, Financeiro, Diretoria, etc.,
> antes uma lista Python hardcoded) e "Departamento" (destino do chamado, já era a tabela
> `departamentos`) passam a ser o **mesmo catálogo**. Nova coluna `departamentos.recebe_chamados`
> (`boolean`, default `false`) marca quais setores têm fila de atendimento — hoje só `TI`/`RH`/
> `Marketing` — e por isso podem ser **destino** de chamado; os demais (Comercial, Financeiro,
> Controladoria, Diretoria, Dpto Químico, SIG, Brigadistas) só identificam **de onde** alguém é.
> - **Todo usuário tem um `departamento_id`** agora (não só staff) — atribuído no cadastro
>   (`/admin/usuarios`), inclusive **Funcionário/CLIENTE**. `OPERADOR` continua exigindo um setor
>   com `recebe_chamados = true` (não há fila pra atender num setor sem staff).
> - **Líder de setor** = `ADMIN` com `departamento_id`, mesmo num setor **sem fila** (ex.: um gestor
>   do Comercial). RLS (`chamados_select`/`mensagens_select`/`historico_select`, migration `0028`)
>   passa a deixar um `ADMIN` **ver** (só leitura) os chamados cujo **autor** pertence ao mesmo
>   `departamento_id` — mesmo que o chamado seja destinado a outro setor. Quem **atende** (muda
>   status, responde, atribui) continua sendo exclusivamente o staff do departamento de **destino**
>   do chamado — TI incluído, por conta da `0020` acima; a UI (`workspace/atendimento.html`) esconde
>   as ações quando `chamado.departamento_id != perfil.departamento_id`.
> - **Guarda-corpo:** trigger `enforce_departamento_recebe_chamados` (só em `chamados.departamento_id`
>   — o de `perfis.departamento_id` foi removido na `0028`, já que agora qualquer setor ativo é uma
>   origem válida de usuário) impede que um chamado seja roteado para um setor sem fila.
>
> ### 🔁 `[DECISÃO DE PRODUTO 2026-07-10]` `0038`/`0042` — Autoatendimento (Marketing, RH)
>
> `departamentos.autoatendimento` (`boolean`, default `false`) marca setores que não funcionam como
> suporte clássico (alguém de fora abre, o setor atende) — são um quadro estilo Trello onde o
> **próprio time** cria e gerencia as demandas. Hoje: **Marketing** (`0038`) e **RH** (`0042`). Nesses
> setores, a trava geral "autor nunca atende o próprio chamado" (Fase 0/`0029`) **não se aplica**:
> `chamados_update_staff` permite `operador_id = cliente_id` e `mensagens_insert` permite o autor
> postar como staff (pública ou nota interna) no próprio chamado. Fora desses dois setores (TI
> incluído), a trava normal continua valendo.
>
> ### 🔁 `[DECISÃO DE PRODUTO 2026-07-20]` `0047` — Autoatendimento generalizado a TODOS os setores
>
> Pedido do usuário: o TI (e qualquer outro setor) não conseguia abrir chamado **para si mesmo** de
> forma útil — o chamado ficava de fora do Kanban/fila (`FilaRepo.fila` só mostra pedido "de fora do
> setor", salvo exceção) e o autor não podia se autoatender. Isso trava um caso real: a diretoria quer
> abrir um chamado (ex.: para o TI) sem entrar na plataforma como staff — alguém do próprio setor abre
> em nome da demanda, só que ela precisa aparecer no quadro do setor como qualquer outra, igual já
> acontecia no Marketing/RH. `departamentos.autoatendimento` passa a `true` para **todos** os setores
> (`UPDATE departamentos SET autoatendimento = true`, `0047`) e o **default da coluna** também vira
> `true` (setor novo cadastrado depois já nasce com a regra, sem UPDATE manual). Nenhuma policy SQL
> nova: `chamados_update_staff`/`mensagens_insert` (`0042`) já liam a coluna de forma genérica, sem
> nome de setor hardcoded — só a **coluna de dados** mudou. Efeito prático: qualquer setor agora é, ao
> mesmo tempo, suporte (recebe chamado de fora) **e** quadro Trello (gerencia as próprias demandas) —
> a distinção "suporte clássico vs. autoatendimento" descrita acima deixa de ser por setor e passa a
> valer por chamado (quem abriu é do mesmo setor de destino, ou não).

Matriz consolidada (Seção 4.3/5 da spec, **reescrita** no modelo vigente — `0020`/`0027`/`0028`/`0038`/`0042`/`0047`):

O eixo deixou de ser só "TI = tudo, resto = só o setor": hoje depende de **três coisas** — o setor de
**destino** do chamado, o setor de **origem** (autor) de quem está olhando, e se esse setor tem
`autoatendimento`. TI **não** tem mais tratamento especial de leitura/escrita (`0020`) — só mantém a
exclusividade sobre gestão de catálogos/usuários/planos.

| Recurso / Ação | Funcionário (CLIENTE) | Staff (OPERADOR/ADMIN) do setor de **destino** | ADMIN **líder de setor** sem fila (`recebe_chamados=false`) | Exclusivo **TI** |
|---|---|---|---|---|
| Ver chamados que **ele mesmo** abriu | Sim | Sim | Sim | — |
| Ver fila do setor (chamados "de fora", `fila()`) | — | Sim, só do **próprio** setor de destino (TI incluído, `0020`) | Não (sem fila) | — |
| Ver chamados abertos por **colega do mesmo setor de origem**, p/ qualquer destino (`chamados_departamento()`) | — | Não (só a própria fila) | **Sim, só leitura** (`0028`) | — |
| Atender: mudar status/prioridade/atribuir, responder | Não | Sim, só no seu setor de **destino** | **Não** (view-only; UI esconde as ações) | — |
| **Autoatendimento** (ser responsável + postar como staff no próprio chamado) | Sim, em qualquer setor (`autoatendimento=true` por padrão em todos desde `0047`; era só Marketing/RH em `0038`/`0042`) | idem | — | — |
| Transferir chamado p/ outro departamento | Não | Não | Não | **Sim**, mas só em chamado já na fila do TI (`WITH CHECK auth_is_ti()`, `USING` não abre exceção) |
| Ver `is_interna = true` | Não | Sim (do seu setor) | Sim (dos que enxerga por `0028`) | — |
| Criar nota interna | Não | Sim | Não | — |
| Avaliar chamado (1–5 ★, CSAT) | **Só o autor**, quando `RESOLVIDO` | Não | Não | — |
| Gerir Departamentos/Categorias/Subcategorias/Planos de SLA | Não | Não | Não | **Sim, exclusivo** |
| Cadastrar/promover usuários | — (direto no Supabase) | — | — | Via `registro_usuarios.sql` (dual-write `perfis`+`app_metadata`) |
| Acesso a Storage de anexos (`chamados-anexos`) | Dos próprios (path-scoped) | Do seu setor | Dos que enxerga (leitura) | Do seu próprio setor (TI não tem mais acesso geral, `0020`) |

> A tabela anterior (modelo `0010`, "TI = acesso total" a qualquer chamado) ficou **historicamente
> incorreta** desde a `0020` (2026-07-06) e só foi corrigida nesta revisão (Sprint 2 / item 2.7, B4) —
> ela chegou a induzir um bug real documentado no changelog de 2026-07-08 (bypass incondicional do TI
> na tela de atendimento, pego antes de produção pela RLS). Fonte de verdade em caso de dúvida: as
> policies SQL da Seção 3.3 e as migrations citadas, não esta tabela.

### 3.3 Políticas RLS por papel (a implementar como SQL nas migrations)

RLS **habilitado nas 8 tabelas** (as 7 canônicas + `departamentos`). Princípio do menor privilégio. **🔁 A partir de `0008`, o isolamento de chamados é por DEPARTAMENTO/AUTOR (não mais por empresa):**

- **CLIENTE (funcionário)**
  - `SELECT chamados`: **`cliente_id = auth.uid()`** (só os que abriu).
  - `INSERT chamados`: `cliente_id = auth.uid()` **e** `departamento_id IS NOT NULL` (escolhe o destino).
  - `SELECT mensagens`: do próprio chamado **e** `is_interna = false`.
  - **Sem** `UPDATE` de status/prioridade/operador.
  - **`UPDATE chamados` (avaliação)**: apenas no chamado próprio e só com `status = 'RESOLVIDO'` (policy `chamados_update_cliente_avaliacao`). O trigger `enforce_cliente_so_avaliacao` (Seção 5.3) trava as colunas — incluindo `departamento_id` (o autor não pode redirecionar o chamado).
- **OPERADOR / ADMIN departamental (staff com `departamento_id` = X)**
  - `SELECT/UPDATE chamados`: **apenas onde `departamento_id = auth_departamento_id()`**. `UPDATE` em `status`, `prioridade`, `operador_id`.
  - `SELECT/INSERT mensagens` (incl. `is_interna`) e `historico`: restritos ao mesmo escopo de departamento.
- **`[REFINADO 0010, RESTRINGIDO 0020]` Staff no departamento `TI`**
  - `SELECT/UPDATE chamados`, `mensagens`, `historico`: **só do próprio setor (TI)**, igual a qualquer outro departamento — a `0020` revogou o "vê/atende tudo" da `0010` (mantido só para gestão de catálogos/usuários/planos, via `auth_is_ti()`, e para o **repasse**: `WITH CHECK auth_is_ti()` permite gravar um `departamento_id` diferente do atual, mas o `USING` da policy de `UPDATE` só libera linhas **já** no setor TI — TI não pode atender um chamado alheio sem antes repassá-lo pra si).
  - Substitui o "super-admin = ADMIN com `departamento_id` NULO" da `0008`.
- **RH / Marketing (staff)**: além do seu setor, veem também os chamados **que abriram** (cláusula `cliente_id = auth.uid()` OR `departamento_id = auth_departamento_id()`).
- **🔁 `[0027/0028]` Líder de setor** (`ADMIN` com `departamento_id`, **mesmo num setor sem fila** — `recebe_chamados = false`, ex.: Comercial)
  - `SELECT` (só leitura) em `chamados`/`mensagens` (`is_interna = false`)/`historico` cujo **autor** (`chamados.cliente_id`) tem `perfis.departamento_id = auth_departamento_id()` — mesmo que o chamado seja destinado a **outro** setor. Não altera `UPDATE`/`INSERT` de forma alguma: quem atende continua sendo só o staff do departamento de **destino**.
  - Setor de origem passou a ser obrigatório pro cadastro de **qualquer** usuário (`/admin/usuarios`), não só staff — é o que torna esse "time" identificável pro líder.

Helpers para as policies (SECURITY DEFINER, evitam recursão de RLS ao ler `perfis`): `auth_role()`, `auth_empresa_id()`, `auth_departamento_id()` e **🔁 `auth_is_ti()`** (o perfil do `auth.uid()` está no departamento `TI`?).

> **✅ Validação e2e (2026-07-01):** contra o Supabase live, simulando os claims JWT de cada usuário via `SET LOCAL ROLE authenticated` + `set_config('request.jwt.claims', …)` (mesmo mecanismo do app), confirmou-se: funcionário vê só o próprio; RH vê o setor RH + os que abriu (mesmo em Marketing) e **não** vê Marketing alheio; RH atualiza só o próprio setor; TI vê tudo; nota interna não chega ao funcionário autor. Dados de teste revertidos. **⚠️ Parcialmente superado pela `0020`** (TI deixou de "ver tudo" pouco depois, 2026-07-06) — sem nova rodada de validação e2e registrada aqui até a `0028`, cuja checagem foi feita direto em produção (ver changelog 2026-07-08): consultas nos dados reais confirmaram que um líder de TI só enxergava chamados próprios + de colegas de TI (nunca de outros setores sem relação), e que o botão de atender um chamado fora do próprio setor (bug desta sessão) não alterava nenhuma linha no banco (RLS bloqueou a escrita mesmo com o botão exposto).

### 3.4 Sessão e Auth (server-rendered) — `[LACUNA]`, a spec não define

- **Token em cookie `httpOnly` + `Secure` + `SameSite=Lax`** (`Strict` onde não houver navegação cross-site necessária). **Nunca** em `localStorage` nem header `Bearer` no browser.
  - `[DECISÃO]` `SameSite=Lax` como padrão (compatível com navegação normal e POSTs same-site do HTMX); reservar `Strict` para o cookie de refresh, se separado.
- **Conteúdo do cookie:** `[DECISÃO]` armazenar o **access token JWT do Supabase** (curto) no cookie de sessão e o **refresh token** em cookie separado `httpOnly`/`Secure`/`SameSite=Strict`. Alternativa: sessão opaca server-side assinada com `itsdangerous` referenciando os tokens — preferível se quisermos revogação imediata. `⚠️ A VALIDAR` na Fase 2.
- **Refresh:** middleware verifica expiração do access token; se expirado e refresh válido, renova via `supabase-py` (`auth.refresh_session`) e re-seta cookies. Falha → redireciona a `/login`.
- **Logout:** invalida sessão no Supabase (`auth.sign_out`) e **limpa os cookies** (expira no passado). 
- **Expiração:** access token conforme config do Supabase (ex.: 1h); sessão de aplicação expira com o refresh token. Inatividade prolongada → re-login.
- **🔁 Cadastro (sistema interno):** **não há signup público**. Colaboradores são criados **direto no Supabase** (Authentication > Users); `handle_new_user` gera o perfil `CLIENTE` vinculado à org interna; a promoção a `OPERADOR`/`ADMIN` e a atribuição de **departamento** são feitas por SQL (`supabase/registro_usuarios.sql`). Não existe rota `/cadastro`.

### 3.4.1 Política de senha e MFA (Sprint 2 / item 2.8, B6)

**Hashing:** delegado por completo ao GoTrue (Supabase Auth) — bcrypt gerenciado pela
plataforma, sem parâmetro de custo/algoritmo exposto do nosso lado. Não há ação de código
a tomar aqui além de manter essa delegação (é a própria decisão, não uma lacuna).

**Política de senha — DECISÃO (2026-07-16):** mínimo de **8 caracteres**, sem exigência de
composição (maiúscula/símbolo/dígito obrigatórios) — segue **NIST 800-63B** (comprimento é
mais eficaz que regras de composição arbitrárias, que na prática induzem padrões
previsíveis tipo "Senha123!"). Já era o comportamento real da aplicação nos dois pontos que
criam/trocam senha; consolidado em `app/security/password_policy.py::SENHA_MIN_CHARS` (fonte
única — antes `app/auth/routes.py` e `app/routes/admin.py` redefiniam a mesma constante `8`
cada um por conta própria, a mesma classe de duplicação já corrigida noutros itens deste
plano). **`[AÇÃO DO GESTOR PENDENTE]`:** replicar o mesmo mínimo no painel do Supabase
(Authentication → Policies → *Password minimum length*, hoje no default `6` do projeto
hospedado) — o GoTrue é a única barreira real para quem chamar a Auth API diretamente, fora
do nosso formulário; não há Management API exposta via MCP para automatizar esse ajuste.

**MFA — Fase 1 IMPLEMENTADA (Sprint 3 / item 3.3, 2026-07-16).** Decisões e alternativas
descartadas estão na **[ADR-0007](docs/adr/0007-mfa-totp-aal2-admin.md)**; o resumo
operacional é este:

- **TOTP nativo do GoTrue**, sem trocar de provedor. O **segredo TOTP vive só no GoTrue** —
  nada no nosso banco. O app orquestra `enroll → challenge → verify` (`app/auth/mfa.py`) com
  um cliente isolado por operação (mesma razão do fluxo de redefinição de senha: as chamadas
  MFA mutam a sessão do GoTrue).
- **Telas** (`app/routes/mfa.py`, `app/templates/mfa/`): `GET /mfa` (hub/estado),
  `POST /mfa/enroll` (QR + segredo exibidos **uma vez**), `POST /mfa/enroll/confirmar`,
  `GET|POST /mfa/verify` (step-up). Páginas standalone no estilo do `/login` — a verificação
  acontece antes de o usuário chegar ao shell.
- **Enforcement (`aal2`) no painel ADMIN** (`admin_context`, `app/auth/dependencies.py::enforce_admin_mfa`):
  - `ADMIN` **com** MFA + sessão `aal1` ⇒ redirect para `/mfa/verify` (`MfaChallengeRequired`;
    HTMX recebe `HX-Redirect`).
  - `ADMIN` **sem** MFA ⇒ entra com **nudge** na UI (Fase 1 = *opcional com aviso*,
    `[DECISÃO DO GESTOR]` 2026-07-16 — não travar admin no dia do deploy).
  - `OPERADOR`/`CLIENTE` ⇒ **fora** do enforcement nesta fatia.
  - No login, quem já tem fator verificado vai direto ao step-up (os fatores vêm na resposta
    do `sign_in_with_password` — sem chamada extra).
- **Como o enforcement sabe que há MFA, sem ir à rede:** um booleano espelhado em
  **`app_metadata.mfa_enabled`** (Admin API), que o GoTrue embute no JWT — mesmo padrão de
  dual-write do `app_metadata.role` (item 1.5). Lido junto do claim `aal`, local, **sem
  migration e sem tocar RLS** (ver ADR-0007 para as alternativas descartadas: chamada por
  request e coluna `perfis.mfa_enabled`).
- **Recovery = reset por admin/TI** (`[DECISÃO DO GESTOR]` 2026-07-16):
  `POST /admin/usuarios/{id}/reset-mfa` remove o(s) fator(es) via Admin API e o usuário
  re-enrola. **Sem recovery codes** (o GoTrue não os gera; construí-los seria guardar mais um
  segredo sob nossa guarda) e **sem reset por e-mail** (quem controla a caixa contornaria o
  MFA).
- **Lockout / janela de re-desafio = padrões do GoTrue** (`[DECISÃO DO GESTOR]` 2026-07-16):
  `aal2` vale pela vida da sessão (persiste no refresh); tentativas são limitadas pelo próprio
  GoTrue. Nossos endpoints mantêm rate limit de borda (`10/minute`, Seção 2.4).
- **Fase 2 (alvo: Sprint 4, condicionada à adoção):** obrigatório para `ADMIN` — vira remover
  o ramo do nudge e mandar todo `aal1` ao enroll. Reavaliar `OPERADOR`/`CLIENTE` conforme a
  sensibilidade dos dados de RH/Financeiro trafegados no portal.
- **`[AÇÃO DO GESTOR PENDENTE]`:** habilitar TOTP no projeto Supabase **hospedado**
  (Authentication → Multi-Factor Authentication). O `supabase/config.toml` local já está com
  `[auth.mfa.totp] enroll_enabled/verify_enabled = true`; sem o equivalente no hospedado, o
  `POST /mfa/enroll` responde 503 com mensagem explicando. Ver
  [`docs/runbook_hardening_gestor.md`](docs/runbook_hardening_gestor.md).

### 3.5 CSRF (ausente na spec — **obrigatório**)

- Proteção CSRF em **todas as mutações** HTMX (`POST/PUT/PATCH/DELETE`).
- **Padrão: double-submit cookie + header.** Token CSRF gerado pelo servidor (assinado com `itsdangerous`), entregue em cookie legível e ecoado pelo cliente em header **`X-CSRF-Token`**. Em HTMX, injetar globalmente via `hx-headers` no `<body>` (lendo o cookie) ou em campo hidden incluído nos forms.
- Servidor valida que o header bate com o cookie/assinatura em toda mutação. `SameSite=Lax/Strict` é defesa complementar, **não** substitui o token.

### 3.6 Verificação do JWT do Supabase — `[LACUNA — DECIDIR]`

- Opções: **HS256** com JWT secret legado **vs** **signing keys assimétricas (RS256/ES256) via JWKS**.
- **DECISÃO: signing keys assimétricas via JWKS** quando o projeto Supabase suportar (verificação por chave pública, sem distribuir segredo simétrico). O backend busca e **cacheia o JWKS** (com rotação) e valida assinatura, `exp`, `aud`, `iss`.
  - **Fallback:** se o projeto ainda usar o **JWT secret legado (HS256)**, validar com esse secret carregado de env var. `⚠️ A CONFIRMAR` qual modo o projeto Supabase provisionado oferece no setup (Fase 1).
- **Segredos:** JWT secret / config de JWKS, `SUPABASE_URL`, `anon key`, `service_role key` e `DATABASE_URL` ficam em **variáveis de ambiente do Railway** (Pydantic Settings). `.env` nunca commitado.

### 3.7 — (reservado) ver 3.8

### 3.8 Security headers e interação com Alpine/HTMX

Headers obrigatórios em todas as respostas (middleware):

- **CSP** (Content-Security-Policy) — estrita:
  - `default-src 'self'`; `script-src 'self'` (assets self-hosted com SRI); `style-src 'self'`; `img-src 'self' data:`; `connect-src 'self' https://<projeto>.supabase.co wss://<projeto>.supabase.co` (Realtime); `frame-ancestors 'none'`; `object-src 'none'`; `base-uri 'self'`.
  - **Sem `unsafe-eval` e sem `unsafe-inline` em `script-src`.** Por isso:
    - **Alpine.js → CSP build obrigatório** (Seção 0.4): o build padrão usa `eval`/`Function`, bloqueado pela CSP.
    - **HTMX:** evitar handlers inline que exijam `unsafe-inline`/`eval`; usar atributos HTMX declarativos e `hx-on:` (sintaxe 2.0) compatíveis com `script-src 'self'`. Se algum recurso exigir inline, usar **nonce** por request em vez de afrouxar a CSP globalmente.
- **HSTS:** `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`.
- **X-Frame-Options:** `DENY` (reforça `frame-ancestors 'none'`).
- **X-Content-Type-Options:** `nosniff`.
- Complementos: `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` mínima.

### 3.9 Storage (`chamados-anexos`, privado)

- Bucket **privado** (nunca público) — exigência da spec (risco Crítico).
- **Signed URLs com TTL de 1h**, geradas no backend **por renderização** (sem cache além do TTL — ver contradição C2). Geração *on-demand* no endpoint que serve o fragmento do anexo.
- **RLS em `storage.objects`** com path **tenant-scoped**: convenção **`{empresa_id}/{chamado_id}/{arquivo}`**. Policies garantem que CLIENTE só acessa objetos sob o prefixo da própria empresa; OPERADOR/ADMIN conforme matriz.
- **Validação de upload server-side (obrigatória):**
  - **Limite 10MB** por arquivo (rejeitar antes de persistir; checar `Content-Length` e tamanho real do stream).
  - **Limite de 20 arquivos por envio** (`app/anexos.py::MAX_ANEXOS`, era 5 até 2026-07-29) — vale para todos os tipos da allow-list e para os três forms com anexo (abertura, chat do Portal, chat do Workspace) e o inbound de e-mail. Exposto ao Jinja como global `MAX_ANEXOS` para os textos de UI não repetirem o número.
  - **Allow-list de tipos:** `pdf, jpg, png, mp4, docx, xlsx, pptx`.
  - **Validação do MIME real por *magic bytes*** (`python-magic`) — **não** confiar no `Content-Type`/extensão enviados pelo cliente.
  - **Sanitização do nome de arquivo** (remover path traversal, normalizar; preferir nome gerado/UUID + extensão validada).
  - **Idempotência contra duplo-submit** (token de submissão único por upload / dedupe por hash do conteúdo no mesmo chamado).

### 3.9.1 Storage (`avatares`, público) — decisão registrada (Sprint 2 / item 2.9-B2)

Diferente de `chamados-anexos`, o bucket **`avatares`** (migration `0033`) é
**intencionalmente público**: path fixo `{user_id}/avatar.png`, sem signed
URL, servido direto via `<img src>` em todo card (fila/kanban/detalhe) —
signed URL giraria a cada TTL e quebraria o cache de imagem do navegador sem
ganho real, já que uma foto de perfil não é dado sensível como um anexo de
chamado. **Reavaliar se a sensibilidade mudar** — ex.: se o avatar passar a
carregar metadado que não deva ser público, ou se a política de privacidade
da empresa exigir todo storage privado por padrão.

### 3.10 OWASP / práticas transversais

- **Validação de input com Pydantic** em todo corpo/query/form.
- **Escape de HTML por padrão no Jinja2** (autoescape ligado; `|safe` só com conteúdo comprovadamente seguro e sanitizado).
- **SQL parametrizado** em 100% das queries (`asyncpg` com placeholders `$1...`); nas migrations/funcs, evitar SQL dinâmico concatenado; usar `format()`/`quote_ident` apenas onde estritamente necessário.
- Mensagens de erro sem vazar stack/segredos ao cliente (tratamento centralizado — Seção 6).

---

## Seção 4 — Testes e QA

### 4.1 TDD adaptado

Nenhuma rota ou função complexa é considerada **"concluída"** sem suíte em **`pytest` + `pytest-asyncio`**. "Definição de pronto" por fase inclui testes verdes (Seção 6).

### 4.2 Estratégia correta (corrige a spec)

A spec (mitigação de risco) sugere "cache em memória/mocks"; para o que importa em segurança, **não mockar o client Supabase**. Estratégia:

- **Testes de integração contra Supabase local** (`supabase start`), exercendo **RLS de verdade**:
  - Inclui o **teste de isolamento multi-tenant da Fase 2**: dois clientes de **empresas diferentes** **não** se enxergam — verificado **via API e via UI** (asserções nas respostas HTML/HTMX, não só no banco).
  - Testar cada política: CLIENTE não vê `is_interna`; CLIENTE não atualiza status; OPERADOR vê tudo; ADMIN irrestrito.
- **Unit tests** para **lógica pura** (matemática de SLA incl. a regra URGENTE=50% e a escada de fallback; geração/format do código `BOND-YYYY-NNNNN`; formatações de data/TZ) — aqui mocks são adequados.
- **Impersonação de usuários nos testes:** injetar **claims JWT** correspondentes a cada papel/empresa e abrir a transação com `SET LOCAL ROLE authenticated` + `set_config('request.jwt.claims', ...)` (mesmo mecanismo da Seção 3.1), validando as policies como o app as usa em produção. Para casos de Auth, gerar tokens válidos contra o Supabase local.

### 4.3 Isolamento de testes

- **Fixtures transacionais com rollback**: cada teste roda em transação revertida ao final — sem efeitos colaterais entre testes.
- **Nunca mutar staging/produção** a partir de testes. Banco de teste = Supabase local efêmero.

---

## Seção 5 — Lógicas de Negócio Críticas (Schema Canônico)

> **Esta é a única referência de modelagem do projeto.** Toda query e todo template referenciam este schema. Nada de "esquema implícito".

### 5.1 Schema canônico — DDL consolidado (7 tabelas)

> O DDL abaixo consolida a spec (Seção 4) e preenche tipos/FKs/índices onde a spec é sintética. Itens não definidos pela spec estão como `[DECISÃO]` ou `⚠️ A VALIDAR`. **Este é o contrato de modelagem; as migrations reais (Fase 1/2) devem materializá-lo.**

#### Enums

```sql
CREATE TYPE papel_usuario  AS ENUM ('ADMIN', 'OPERADOR', 'CLIENTE');
CREATE TYPE prioridade_chamado AS ENUM ('BAIXA', 'MEDIA', 'ALTA', 'URGENTE');   -- [DECISÃO] níveis; URGENTE derivado (50% ALTA)
CREATE TYPE status_chamado  AS ENUM ('NOVO', 'EM_ATENDIMENTO', 'AGUARDANDO', 'RESOLVIDO');  -- da spec (badges Seção 5.1)
```

> `⚠️ SUPOSIÇÃO A VALIDAR:` o conjunto de prioridades (`BAIXA/MEDIA/ALTA/URGENTE`). A spec só nomeia ALTA e URGENTE explicitamente; as demais são `[DECISÃO]`. Confirmar com o gestor junto da validação de SLA.

#### 1. `planos_sla`

```sql
CREATE TABLE planos_sla (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome              text NOT NULL,                 -- Bronze, Ouro, etc.
  -- tempos em MINUTOS, por prioridade (ALTA é base; URGENTE = 50% de ALTA, não armazenado)
  resposta_baixa_min     integer,
  resposta_media_min     integer,
  resposta_alta_min      integer,
  resolucao_baixa_min    integer,
  resolucao_media_min    integer,
  resolucao_alta_min     integer,
  -- defaults do plano (escada de fallback, contradição C1, passo 3)
  resposta_default_min   integer,                  -- [DECISÃO]
  resolucao_default_min  integer,                  -- [DECISÃO]
  ativo             boolean NOT NULL DEFAULT true,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);
```

> `⚠️ A VALIDAR:` unidade armazenada (minutos) e quais prioridades têm linha de tempo. URGENTE **não** tem coluna — é derivada (50% de ALTA) no `calcular_sla_chamado`.

#### 2. `empresas`

```sql
CREATE TABLE empresas (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome_fantasia   text NOT NULL,
  cnpj            text UNIQUE,                     -- ⚠️ A VALIDAR formato/validação
  plano_sla_id    uuid REFERENCES planos_sla(id) ON DELETE RESTRICT,
  ativo           boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_empresas_plano ON empresas(plano_sla_id);
```

#### 3. `perfis` (extensão de `auth.users`)

```sql
CREATE TABLE perfis (
  id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  nome        text,
  role        papel_usuario NOT NULL DEFAULT 'CLIENTE',   -- handle_new_user define CLIENTE
  empresa_id  uuid REFERENCES empresas(id) ON DELETE RESTRICT,  -- ADMIN/OPERADOR podem ter NULL? ⚠️ A VALIDAR
  ativo       boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_perfis_empresa ON perfis(empresa_id);
CREATE INDEX idx_perfis_role ON perfis(role);
```

> `⚠️ A VALIDAR:` se OPERADOR/ADMIN têm `empresa_id` NULL (equipe interna Bondmann) ou pertencem a uma empresa "matriz". Impacta policies que derivam `empresa_id` do perfil.

#### 4. `categorias`

```sql
CREATE TABLE categorias (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome        text NOT NULL,
  descricao   text,
  ativo       boolean NOT NULL DEFAULT true,        -- ativação/desativação sem exclusão (spec 5.3)
  created_at  timestamptz NOT NULL DEFAULT now()
);
```

> `⚠️ A VALIDAR:` categorias são **globais** ou **por-tenant**? A spec trata como catálogo global gerido por ADMIN. **`[DECISÃO]`: globais** (sem `empresa_id`). Se vierem a ser por-tenant, o cache (2.3) já é tenant-scoped e basta adicionar a coluna.

#### 5. `chamados` (entidade central)

```sql
CREATE TABLE chamados (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo           text UNIQUE NOT NULL,            -- BOND-YYYY-NNNNN (gerar_codigo_chamado)
  empresa_id       uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  cliente_id       uuid NOT NULL REFERENCES perfis(id) ON DELETE RESTRICT,    -- = auth.uid() na criação
  operador_id      uuid REFERENCES perfis(id) ON DELETE SET NULL,
  categoria_id     uuid REFERENCES categorias(id) ON DELETE RESTRICT,
  titulo           text NOT NULL,
  descricao        text NOT NULL,
  status           status_chamado NOT NULL DEFAULT 'NOVO',
  prioridade       prioridade_chamado NOT NULL DEFAULT 'MEDIA',
  limite_resposta  timestamptz,                     -- calculado por calcular_sla_chamado
  limite_resolucao timestamptz,                     -- calculado por calcular_sla_chamado
  respondido_em    timestamptz,                     -- primeira resposta do operador (para conformidade SLA)
  resolvido_em     timestamptz,
  -- Avaliação (CSAT) do AUTOR, só quando RESOLVIDO (migration 0006 / Fase 3)
  avaliacao_nota       smallint CHECK (avaliacao_nota BETWEEN 1 AND 5),
  avaliacao_comentario text,
  avaliacao_em         timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_chamados_empresa      ON chamados(empresa_id);
CREATE INDEX idx_chamados_status       ON chamados(status);
CREATE INDEX idx_chamados_operador     ON chamados(operador_id);
CREATE INDEX idx_chamados_prioridade   ON chamados(prioridade);
CREATE INDEX idx_chamados_limite_resol ON chamados(limite_resolucao);
CREATE INDEX idx_chamados_empresa_status ON chamados(empresa_id, status);  -- fila filtrada por tenant
CREATE INDEX idx_chamados_avaliacao ON chamados(avaliacao_nota) WHERE avaliacao_nota IS NOT NULL;  -- KPI CSAT
```

> **`[DECISÃO DE PRODUTO]` Abertura por Categoria + Assunto (sem "Produto"):** o
> chamado é classificado por **categoria** (`categoria_id`) e **assunto**
> (`titulo`) — **não** existe dimensão de "produto" na abertura. O protótipo
> aprovado trazia um campo "Produto relacionado" (lista de sub-marcas BD …) que
> foi **removido** na aprovação; o schema canônico nunca teve essa coluna, então
> a decisão apenas confirma o modelo. A tela de produção (`/portal/chamados/novo`)
> e o protótipo de referência foram alinhados a essa regra.

> **`[DECISÃO]` Avaliação (CSAT) no próprio `chamados`:** a nota 1–5 do autor é
> persistida em `avaliacao_nota` (+ comentário e timestamp) na própria linha do
> chamado, não em tabela à parte — leitura simples para o histórico do cliente e
> fonte direta do KPI CSAT (Fase 5), sem depender de e-mail transacional. Regras
> de quem/quando avalia: Seções 3.2/3.3; trava de coluna: Seção 5.3.

#### 6. `mensagens`

```sql
CREATE TABLE mensagens (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chamado_id    uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  remetente_id  uuid NOT NULL REFERENCES perfis(id) ON DELETE RESTRICT,
  conteudo      text NOT NULL,
  is_interna    boolean NOT NULL DEFAULT false,      -- true = invisível ao CLIENTE
  anexos        jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{path, nome, mime, tamanho}] no bucket privado
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_mensagens_chamado ON mensagens(chamado_id, created_at);
CREATE INDEX idx_mensagens_interna ON mensagens(chamado_id) WHERE is_interna = false;
```

> `⚠️ A VALIDAR:` anexos como `jsonb` na mensagem (a spec lista `anexos[]` em `mensagens`). Alternativa normalizada: tabela `anexos` própria. **`[DECISÃO] MVP:`** `jsonb` em `mensagens` (simples, alinhado à spec); só os **paths** ficam no banco, os bytes no Storage.

#### 7. `historico_chamados` (auditoria imutável)

```sql
CREATE TABLE historico_chamados (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id  uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  ator_id     uuid REFERENCES perfis(id) ON DELETE SET NULL,
  acao        text NOT NULL,                          -- ex.: STATUS_ALTERADO, ATRIBUIDO, PRIORIDADE_ALTERADA
  detalhes    jsonb NOT NULL DEFAULT '{}'::jsonb,     -- {de, para, ...}
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_historico_chamado ON historico_chamados(chamado_id, created_at);
```

> Imutabilidade: sem `UPDATE`/`DELETE` em política normal (apenas `INSERT`/`SELECT`); reforçar via RLS e, opcionalmente, revogar `UPDATE/DELETE` no nível de grants.

#### 🔁 8. `departamentos` + colunas de roteamento (migration `0008`)

Adendo ao schema canônico para o modelo interno (Seção 3.2). **8ª tabela** e duas colunas:

```sql
CREATE TABLE departamentos (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome            text NOT NULL UNIQUE,          -- seed: TI, RH, Marketing (gerenciável)
  ativo           boolean NOT NULL DEFAULT true,
  -- 🔁 0027: true = tem fila/staff, pode ser DESTINO de chamado (TI/RH/Marketing).
  -- false = setor só identifica quem abriu (Comercial, Financeiro, Diretoria, Dpto
  -- Químico, Controladoria, SIG, Brigadistas — seed 0027).
  recebe_chamados boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now()
);

-- 🔁 0028: setor de ORIGEM de QUALQUER usuário (não só staff) — funcionário comum
-- inclusive, pra o líder do setor saber quem é da equipe. Antes: NULL = super-admin
-- (ADMIN) ou funcionário (CLIENTE); a coluna virou obrigatória na aplicação p/ todo
-- papel (continua nullable no banco — o trigger handle_new_user cria o perfil sem
-- setor, a promoção seguinte é quem preenche). Guarda-corpo que exigia
-- recebe_chamados=true aqui foi REMOVIDO na 0028 (qualquer setor ativo é origem válida).
ALTER TABLE perfis   ADD COLUMN departamento_id uuid REFERENCES departamentos(id) ON DELETE SET NULL;
-- Setor de DESTINO do chamado (obrigatório na abertura, via RLS/rota). 🔁 0027: trigger
-- enforce_departamento_recebe_chamados barra destino num setor com recebe_chamados=false.
ALTER TABLE chamados ADD COLUMN departamento_id uuid REFERENCES departamentos(id) ON DELETE RESTRICT;
CREATE INDEX idx_chamados_departamento ON chamados(departamento_id);
CREATE INDEX idx_chamados_depto_status ON chamados(departamento_id, status);
```

> **`empresas`/`planos_sla`**: mantidas apenas como **config interna de SLA** (org única
> `Bondmann Química` semeada em `0008`); `empresa_id` continua no schema (path do Storage +
> motor de SLA), mas **não** é mais o eixo de isolamento. Sem venda/gestão self-service.

#### 🔁 9. Combinação de chamados duplicados (migration `0065`)

Adendo ao schema canônico. **`[DECISÃO DE PRODUTO]` 2026-07-30 (gestor):** quando um
incidente atinge todo mundo ao mesmo tempo (servidor/internet/ERP fora do ar), N pessoas
abrem N chamados da MESMA ocorrência. O setor responde N vezes e o indicador conta N
demandas — volume do mês e TMA inflados por um único evento. A feature junta tudo num
chamado só, com as informações dos repetidos, mantendo os autores **em cópia**.

```sql
-- Auto-referência (1:N), não tabela de ligação: a pergunta que o sistema faz o
-- tempo todo é "esta linha conta?", e uma coluna nula responde isso num índice
-- parcial, sem JOIN em toda agregação do Admin.
ALTER TABLE chamados
  ADD COLUMN chamado_principal_id uuid REFERENCES chamados(id) ON DELETE SET NULL,
  ADD COLUMN combinado_em         timestamptz,
  ADD COLUMN combinado_por        uuid REFERENCES perfis(id) ON DELETE SET NULL;
ALTER TABLE chamados ADD CONSTRAINT chamados_combinacao_nao_auto
  CHECK (chamado_principal_id IS NULL OR chamado_principal_id <> id);
CREATE INDEX idx_chamados_principal
  ON chamados(chamado_principal_id) WHERE chamado_principal_id IS NOT NULL;
```

> **`[DECISÃO]` Sem status `COMBINADO` no enum.** Um status novo obrigaria a tocar cada
> `_status_ui`, cada badge, o Kanban de três setores e as triggers de SLA/`RESPOSTA_CLIENTE`
> (`0061`). O duplicado é encerrado como **`RESOLVIDO`** (para o relógio de SLA e sai do
> quadro) e quem o tira dos indicadores é a **coluna**, não o status.

> **`[DECISÃO]` "Em cópia" reaproveita `chamados_observadores` (`0034`) na íntegra.** O autor
> do duplicado — e quem já estava em cópia nele — vira observador do principal e ganha, pela
> RLS que já existe, leitura do chamado + mensagens públicas + sino/Realtime. **Nenhuma
> policy nova.** Limite herdado do modelo de observador: quem está em cópia **lê**, não
> responde (`mensagens_insert`, `0042`) e não recebe e-mail (`notificar_nova_mensagem_email`
> só endereça autor/operador) — ver a pendência `⚠️` ao final desta seção.

> **`[DECISÃO]` Conteúdo consolidado como UMA mensagem pública**, publicada no principal por
> quem combinou (`app/domain/combinacao.py`): cabeçalho + assunto + descrição + os anexos do
> duplicado (mesmos `path`; o bucket é escopado por `empresa_id`, `0007`, então a signed URL
> segue válida sem recopiar bytes). Copiar cada mensagem preservando `remetente_id` exigiria
> conexão administrativa (a policy exige `remetente_id = auth.uid()`) e produziria falas
> antigas surgindo no meio do histórico do principal, sem contexto.
>
> **🔁 `[REVISADO 2026-07-30, mesmo dia — gestor]` Só o DESCRITIVO, sem a conversa.** A versão
> original da digest trazia também as **falas públicas** do duplicado. Na prática isso poluía
> o chat do principal: numa combinação de N chamados, N conversas inteiras eram despejadas de
> uma vez — e o que mais aparecia ali era a troca de **perguntas da triagem por IA** com o
> autor (mensagens públicas como quaisquer outras, `app/ia/triagem.py`), que é raciocínio do
> chamado repetido, não informação nova sobre a ocorrência. A digest passa a ser código +
> autor/contato + data + assunto + descrição, e para aí. **Os anexos continuam vindo** (print
> do erro, log — evidência, não conversa; inclui os da abertura, que moram na primeira
> mensagem). Quem precisar do detalhe abre o duplicado pelo link em "Chamados combinados
> neste". `texto_combinacao` deixou de receber as mensagens: não há como uma fala vazar por
> ela; a consulta do repositório passou a buscar só `mensagens.anexos`.

**Integridade no banco (trigger `enforce_combinacao_chamados`), não só no Python:**
sem correntes (não se combina com quem já é duplicado, nem vira duplicado quem já é
principal), **mesmo `departamento_id` de destino** (combinar entre setores furaria o escopo
de RLS e moveria observadores para fora do alcance de quem combinou) e **CLIENTE nunca
combina**. Este último é o ponto sensível: `enforce_cliente_so_avaliacao` (`0006`/`0059`) é
uma **lista de colunas**, então toda coluna nova nasce liberada ao autor num UPDATE do
próprio chamado — a `0065` acrescenta as três colunas à lista *e* recusa `CLIENTE` no
trigger. Sem isso, um funcionário apontaria o próprio chamado resolvido para qualquer
chamado do setor e se auto-incluiria em cópia nele (vazamento de conteúdo de terceiros).

**Onde o duplicado deixa de contar** (todas as agregações ganham
`chamado_principal_id IS NULL`): KPIs/CSAT/TMA/conformidade/produtividade/gráficos do Admin,
views mensais do Marketing (`0032`, reescritas na `0065`), fila/Kanban/`fila_stats`/
`chamados_departamento`, sino (`notificacoes`), trava de CSAT (`avaliacao_pendente`,
`pode_avaliar`, `pode_reabrir`) e busca de semelhantes da IA (`ia_busca`). **Onde ele
continua aparecendo**, de propósito: "Meus chamados" do autor (com o selo "Combinado com
BOND-…") e o **export CSV** do Admin, que ganha a coluna `Combinado com` — relatório é dado
bruto, e apagar a linha esconderia quantas pessoas o incidente afetou.

### 5.2 SLA — regras

- **URGENTE = 50% do tempo de ALTA** (resposta e resolução), conforme spec.
- **Escada de fallback:** ver contradição **C1** (URGENTE→ALTA→default do plano→fallback global 12h/24h).
- **`[LACUNA — DECIDIR e marcar para validação]`:**
  - **Unidade:** **horas corridas** no MVP. `⚠️ VALIDAR COM O GESTOR` se deve ser **horário comercial + feriados** (mais fiel a SLA real). Se comercial: necessária tabela/calendário de expediente e feriados — fora do MVP a menos que o gestor exija.
  - **Timezone:** **`America/Sao_Paulo`** para exibição e cálculo de janelas; **timestamps sempre em UTC (`timestamptz`) no banco**. Conversão na borda (render/cálculo).
  - **Relógio de SLA (eventos que iniciam/pausam/param):** `[DECISÃO + ⚠️ VALIDAR]`
    - **Inicia:** na **criação** do chamado (`created_at`) → define `limite_resposta` e `limite_resolucao`.
    - **`respondido_em`:** marca cumprimento do SLA de resposta na **primeira mensagem do operador**.
    - **Pausa:** status **`AGUARDANDO`** (aguardando o cliente) **pausa** o relógio de resolução; ao voltar a `EM_ATENDIMENTO`, retoma. `⚠️ VALIDAR` se há pausa ou se o prazo é absoluto desde a criação (impacta o cálculo de conformidade).
    - **Para:** status **`RESOLVIDO`** (`resolvido_em`).
- **🔁 Prazos que NÃO vêm do plano da empresa** (exceções por fluxo, cada uma numa migration própria; a ordem abaixo é a ordem de precedência dentro de `calcular_sla_chamado`):
  1. **`sem_prazo`** (Marketing, `0040`) — sem `limite_resposta`/`limite_resolucao`: não há contagem, o chamado nunca fica "atrasado".
  2. **`status = 'PROJETOS'`** (TI, `0064`, decisão do gestor 2026-07-29) — prazo de resolução próprio, contado da **entrada na coluna**. Projeto é trabalho de fôlego: com as 24h do atendimento reativo, todo card arrastado para "Projetos" (`0057`) nascia vencido. Mudar a **prioridade** de um projeto **não** recalcula o prazo, e sair da coluna **não** o desfaz.
     - **🔁 `[REVISADO 2026-07-30 — gestor]` `0066`: o mês fixo virou prazo CONFIGURÁVEL.** Cada projeto tem um escopo diferente (dois meses, 70 dias, quatro meses), então o número deixa de ser um literal e passa a ter dois níveis: **`chamados.prazo_projeto_dias`** (o prazo daquele projeto, definido pelo operador de TI na tela de atendimento) com fallback em **`planos_sla.projeto_dias`** (padrão do setor, editável em `/admin/gestao`, default **30** = o mesmo mês da `0064` — quem não configurar nada não sente a mudança). **Unidade:** dias **corridos** na interface (é como o gestor pensa o prazo), convertidos para **minutos úteis** no banco pela mesma proporção que a `0064` fixou — `dias_uteis = round(dias × 22/30)`, `minutos = dias_uteis × 10h` —, de modo que o prazo continue caindo sempre em expediente. **Base:** `chamados.projeto_em`, o instante da entrada na coluna, agora materializado: trocar os dias de um projeto que começou há três semanas ajusta a data final **sem recomeçar a contagem de hoje** (sem isso, cada ajuste esticaria o projeto em silêncio). Reentrar na coluna recomeça a contagem, como antes.
  3. **`data_entrega`** (Marketing, `0022`) — o prazo é a data pedida, às 18h de Brasília.

### 5.3 Triggers/Functions (nomes exatos da spec)

1. **`trigger_set_timestamp`** — `BEFORE UPDATE` em `planos_sla`, `empresas`, `perfis`, `chamados`: seta `updated_at = now()`. (`planos_sla` incluída na implementação por também possuir `updated_at`; estende a lista da spec.)
2. **`gerar_codigo_chamado`** — `BEFORE INSERT` em `chamados`: gera `codigo` no formato **`BOND-YYYY-NNNNN`** via **sequence dedicada** (sem `SELECT MAX()+1` — seria *racy*).
3. **`calcular_sla_chamado`** — `BEFORE INSERT` (e em mudança de prioridade) em `chamados`: calcula `limite_resposta`/`limite_resolucao` conforme 5.2 + escada C1. **🔁 `0064`:** respeita as exceções de 5.2 — `sem_prazo` e `PROJETOS` saem antes da escada, para que uma troca de prioridade não derrube o prazo de um projeto. **🔁 `0066`:** o ramo de `PROJETOS` usa o prazo configurado (`prazo_projeto_dias` → `planos_sla.projeto_dias`) e a base `projeto_em`.
3.1. **🔁 `sla_projetos_prazo`** *(migration `0066`, sucede `sla_projetos_um_mes` da `0064`)* — `BEFORE INSERT OR UPDATE OF status, prazo_projeto_dias` em `chamados`: ao ENTRAR em `PROJETOS` carimba `projeto_em` e escreve o prazo (5.2); ao trocar os **dias** de um projeto, reescreve o prazo a partir de `projeto_em` (não de `now()`). Roda depois de `sla_pausa_aguardando` (ordem alfabética de trigger — `sla_pa…` < `sla_pr…`, como era com o nome antigo), então a retomada da pausa não sobrescreve o prazo do projeto.
4. **`handle_new_user`** — `AFTER INSERT` em `auth.users`: cria `perfis` com `role = 'CLIENTE'`. **🔁 `0008`:** vincula o novo usuário à **org interna única** (`empresa_id` = primeira `empresas`), pois o cadastro passa a ser **direto no Supabase** (sem signup público); a promoção de papel/departamento é feita por SQL (`supabase/registro_usuarios.sql`).
5. **🔁 `enforce_cliente_so_avaliacao`** — atualizada em `0008` para incluir `departamento_id` no conjunto imutável (o autor não redireciona o chamado). **🔁 `0065`:** inclui também `chamado_principal_id`/`combinado_em`/`combinado_por` — a trava é uma **lista de colunas**, então toda coluna nova precisa entrar nela explicitamente ou nasce liberada ao autor (ver Seção 5.1, item 9). **🔁 `0066`:** pela mesma razão, inclui `prazo_projeto_dias`/`projeto_em` (sem isso o autor daria a si mesmo o prazo que quisesse).
5.1. **🔁 `enforce_combinacao_chamados`** *(migration `0065`)* — `BEFORE INSERT OR UPDATE OF chamado_principal_id` em `chamados`: recusa corrente de combinações, combinação entre departamentos diferentes e `CLIENTE`; carimba `combinado_em`/`combinado_por` a partir de `auth.uid()` (autoria não vem do POST) e os limpa ao desfazer.
5. **`enforce_cliente_so_avaliacao`** *(adicionado na Fase 3, migration 0006)* — `BEFORE UPDATE` em `chamados`: quando `auth_role() = 'CLIENTE'`, rejeita qualquer `UPDATE` que altere colunas que não sejam de avaliação (`avaliacao_nota`/`avaliacao_comentario`/`avaliacao_em`). Complementa a policy `chamados_update_cliente_avaliacao` (RLS não restringe colunas). `REVOKE EXECUTE` aplicado (não é RPC), como nas demais functions de trigger (Seção 0005).

#### `gerar_codigo_chamado` — concorrência, escopo, reset, overflow `[LACUNA]`

- **Concorrência:** usar **sequence Postgres** (atômica). Nada de `MAX()+1`.
- **Escopo:** **`[DECISÃO]` global** (`BOND-YYYY-NNNNN` único no sistema, não por-tenant) — simplifica unicidade e auditoria; o tenant já está em `empresa_id`. (Se exigirem numeração por-tenant, trocar para uma sequence por empresa — registrar como mudança de schema.)
- **Reset anual:** o `NNNNN` reinicia a cada ano. Implementação robusta sob concorrência: tabela de contador por ano com `INSERT ... ON CONFLICT (ano) DO UPDATE SET ultimo = ultimo + 1 RETURNING ultimo` (atômico), **ou** sequence anual recriada/`ALTER SEQUENCE ... RESTART` de forma controlada. **`[DECISÃO]`: tabela-contador por ano com upsert atômico** (evita corrida de "virada de ano" e não depende de DDL em runtime).
- **Padding:** **5 dígitos** com zero à esquerda (`LPAD(n::text, 5, '0')`).
- **Overflow (> 99.999/ano):** `[DECISÃO]` ao exceder, **expandir para 6 dígitos** automaticamente (o formato passa a `BOND-YYYY-NNNNNN`) e **logar alerta**; nunca falhar a criação do chamado por overflow de padding. `⚠️ VALIDAR` se preferem outra política (ex.: prefixo de bloco).

### 5.4 Migrations

- Todo schema, RLS, triggers e seeds entram como **migrations `.sql` versionadas** via **Supabase CLI** em **`supabase/migrations/`**.
- **Convenção de nomes:** `NNNN_descricao.sql` (timestamp do CLI) — ex.: `0001_init_enums.sql`, `0002_tables_core.sql`, `0003_triggers.sql`, `0004_rls_policies.sql`, `0005_storage_policies.sql`, `0006_seed_planos_categorias.sql`.
- **Ordem:** enums → tabelas → índices → sequences/contadores → functions/triggers → RLS → storage policies → seeds. Migrations imutáveis após merge (nova mudança = nova migration).

---

## Seção 6 — Fluxo de Desenvolvimento e Cronograma Modular

Consolida as **5 fases** da spec (Seção 6 do .docx), preservando os critérios de aceite e adicionando um **checklist de "definição de pronto" (DoD)** verificável por fase.

### Fase 1 — Setup & Infraestrutura (Semana 1)
**Entregáveis (spec):** FastAPI rodando local; conexão Supabase validada; Dockerfile funcional; deploy Railway com URL pública.
**Critério de aceite (spec):** URL pública responde **200 OK** com página de status.
**DoD adicional:**
- [ ] `GET /health` retornando 200 desde já (Railway healthcheck).
- [ ] **Dockerfile testado localmente** (build + run) antes do primeiro push; **Uvicorn na porta 8080**; inclui `libmagic`; **build step do Tailwind CLI** (CSS purgado) no estágio de build.
- [ ] Lockfile com **todas as versões fixadas** (Seção 0); `⚠️ VERSÃO A CONFIRMAR` resolvidas e travadas.
- [ ] Pydantic Settings carregando secrets de env; `.env` no `.gitignore`.
- [ ] Supabase CLI iniciando stack local (`supabase start`); primeira migration (enums + tabelas) aplicada.
- [ ] Pool `asyncpg` via Supavisor **transaction mode (6543)**, `statement_cache_size=0`.
- [ ] Suíte pytest mínima (health, settings) verde.

### Fase 2 — Autenticação & Controle de Acesso (Semana 2)
**Entregáveis (spec):** `/login`, `/logout`, `/cadastro`; middleware JWT validando role; redirecionamento por perfil; RLS ativo.
**Critério de aceite (spec):** login com CLIENTE/OPERADOR/ADMIN — cada um acessa só suas rotas.
**DoD adicional:**
- [ ] Cookies de sessão `httpOnly+Secure+SameSite` (Seção 3.4); refresh e logout funcionando.
- [ ] **CSRF** double-submit em todas as mutações (Seção 3.5).
- [ ] Verificação de JWT (JWKS ou HS256 — Seção 3.6) implementada.
- [ ] **RLS habilitado nas 7 tabelas** + policies por papel (3.3) via migration; acesso de domínio por `asyncpg` + `SET LOCAL` (3.1).
- [ ] Security headers + CSP estrita; **Alpine CSP build** e HTMX 2.0 sem `unsafe-inline/eval`.
- [ ] **Teste de isolamento multi-tenant** (4.2) passando **antes de ir para a Fase 3** (exigência da spec 8.1) — via API e UI.

### Fase 3 — Portal do Cliente (Semanas 3–4)
**Entregáveis (spec):** dashboard do cliente; formulário de abertura com upload; chat do chamado.
**Critério de aceite (spec):** cliente abre chamado, faz upload, vê código gerado e recebe resposta do operador em tempo real.
**DoD adicional:**
- [ ] Dashboard com cards e tabela paginada; badges de status (cores da spec 5.1).
- [ ] Form dinâmico (categoria altera campos via HTMX); upload com validação **server-side** (10MB, allow-list, magic bytes, sanitização, idempotência — 3.9).
- [ ] Anexos via **signed URL TTL 1h on-demand** (C2/3.9); path `{empresa_id}/{chamado_id}/{arquivo}`.
- [ ] Chat funcionando (ver topologia Realtime abaixo); CLIENTE **não** vê `is_interna`.
- [ ] Testes de integração das rotas do cliente com RLS real.

### Fase 4 — Workspace do Operador (Semanas 5–7)
**Entregáveis (spec):** fila Kanban/Lista com SLA visual; tela individual; notas internas; chat Realtime.
**Critério de aceite (spec):** operador vê chamado em **vermelho** após vencimento; responde via chat; **nota interna não aparece para o cliente**.
**DoD adicional:**
- [ ] **SLA validado com o gestor** (C1 / 5.2) **antes desta fase** (exigência spec 8.1).
- [ ] Indicador SLA por cores (verde / amarelo <25% / vermelho piscante <10% ou vencido).
- [ ] Kanban via **Sortable.js** (DnD) + HTMX para persistência; fallback botão de status.
- [ ] Toggle "Nota Interna" (fundo amarelo via Alpine; `is_interna=true` decidido/validado no servidor).
- [ ] Atribuição em lote; ações rápidas (status/operador/prioridade) via HTMX; cada mudança gera `historico_chamados`.

### Fase 5 — Painel Admin & Relatórios (Semanas 8–9)
**Entregáveis (spec):** KPIs com gráficos; gestão de SLAs/Empresas; export CSV.
**Critério de aceite (spec):** admin gera relatório do mês, exporta CSV e altera plano de SLA com efeito imediato nos novos chamados.
**DoD adicional:**
- [ ] KPIs: TMA, conformidade de SLA (% dentro de `limite_resolucao`), **CSAT** (média de `chamados.avaliacao_nota` — coletado no Portal do Cliente desde a Fase 3, **sem depender de e-mail transacional**), produtividade por operador.
- [ ] Gestão de Empresas/Planos/Categorias (ativar/desativar sem excluir histórico); convite de usuários com role.
- [ ] `GET /admin/export/csv` com filtro por datas e campos da spec (5.3).
- [ ] Gráficos com Chart.js 4.

### 6.1 Topologia do Realtime (chat) — `[LACUNA — DECIDIR]`

- **DECISÃO:** o **browser conecta direto ao Supabase Realtime** via **`supabase-js`** (carregado self-hosted), autenticado com o **JWT do usuário**, com **RLS aplicada no canal/tabela `mensagens`** filtrada por `chamado_id`. **Não** haverá bridge WS/SSE no FastAPI para o chat (reduz complexidade e carga no app server).
  - **Justificativa:** Realtime do Supabase já escala com o plano pago (mitigação da spec) e aplica RLS por canal; um bridge no FastAPI duplicaria estado e seria gargalo.
  - **Fonte de verdade:** mensagens **persistidas na tabela `mensagens`** (via POST HTMX para o FastAPI, que valida CSRF/permissão/`is_interna` e insere). O Realtime apenas **entrega** o evento de nova linha aos assinantes do `chamado_id`. Ou seja: **escrita pelo FastAPI; leitura/entrega instantânea pelo Realtime**.
  - **Visibilidade de `is_interna`:** garantir que a **RLS do canal** impeça o CLIENTE de receber mensagens internas (a mesma policy de `SELECT` de `mensagens`). `⚠️ VALIDAR` que o Realtime respeita a policy de `SELECT` para o filtro de `is_interna` na entrega.
  - **Fallback:** se o canal WebSocket instабilizar, **polling HTMX de 5s** no fragmento do chat (documentado na spec). O fragmento de polling deve respeitar ETag/304 (Seção 2.2).

> `⚠️ SUPOSIÇÃO A VALIDAR:` o `supabase-js` no browser, com CSP estrita (`connect-src` incluindo `wss://<projeto>.supabase.co`), conecta ao Realtime sem violar CSP. Validar `connect-src`/`script-src` na Fase 3.

### 6.2 Config & Secrets

- **Pydantic Settings** para toda configuração; **`.env` nunca commitado**; segredos via **env do Railway**.
- **Regra dura:** a **`service_role` key NUNCA chega ao browser** e nunca é usada para servir dados de usuário (Seção 3.1). No browser só vão `SUPABASE_URL` + **anon key** (necessários ao `supabase-js` do Realtime) e o **JWT do usuário**.

> ### 🔒 REGRA DURA DE SEGURANÇA — Chaves de API NUNCA expostas (vale para TODAS as fases)
>
> **Nenhuma chave de API, token, segredo ou string de conexão pode aparecer em texto claro em parte alguma do projeto.** Isto inclui — sem exceção — código-fonte, templates, arquivos de configuração versionados, **este e qualquer outro `.md`/documentação**, comentários, mensagens de commit, descrições de PR, logs, prints/screenshots e exemplos. Aplica-se a `anon key`, **chave publishable/`sb_publishable_...`**, `service_role key`, JWT secret, `DATABASE_URL` e quaisquer credenciais de terceiros.
>
> - **Único local permitido:** variáveis de ambiente (env do Railway em produção; `.env` local **no `.gitignore`**, nunca commitado), carregadas via Pydantic Settings.
> - **Em documentação/exemplos:** usar sempre placeholders (`SUPABASE_ANON_KEY`, `<sua-anon-key>`), nunca o valor real.
> - **Mesmo a `anon key`/publishable** (apesar de "pública") **não** deve ser hard-coded em arquivos versionados — entra no HTML servido **em runtime** a partir de env var, para permitir rotação sem alterar o repositório.
> - **Se uma chave for exposta acidentalmente** (commit, log, chat): tratá-la como **comprometida** → **rotacionar imediatamente** no painel Supabase e purgar do histórico antes de qualquer outra ação.
> - **Chaves recebidas via chat/issue** (como no setup inicial) servem **apenas** para a operação pontual e **não** devem ser persistidas no repositório.

### 6.3 Observabilidade

- **Logging estruturado** (JSON) com **request-id** por requisição (correlação ponta a ponta).
- **Tratamento de erro centralizado** (handlers FastAPI): resposta limpa ao cliente, log completo no servidor, sem vazar stack/segredos.
- **Critério de go-live** (spec 8.2): **48h sem 5xx** nos logs; **backup do banco habilitado** no Supabase; teste de isolamento multi-tenant aprovado; chamado ponta a ponta validado (abertura → atribuição → SLA vencendo → resolução → export CSV).

---

## Seção 7 — Protocolo de Atualização de Contexto (doc vivo)

> **Diretriz para mim mesmo (futuras sessões):** ao final de **cada PR, feature ou correção complexa**, revisar e atualizar este documento para refletir o **estado real** do projeto.

**Mecânica operacional:**

1. **Changelog datado** ([`docs/CHANGELOG.md`](docs/CHANGELOG.md), Sprint 2 / item 2.7-B4): registrar `data · seção alterada · resumo` a cada mudança no doc — **linha mais nova no topo**.
2. **Tabela "Estado de Implementação"** (abaixo): atualizar `feature · status · fase · observações` **a cada entrega**.
3. **Regra de precedência:** se **schema, RLS ou regra de SLA** mudar, **a Seção correspondente (5 / 3 / 5.2) é atualizada ANTES** de prosseguir com o código. O código segue o doc, não o contrário.
4. Toda nova `⚠️ SUPOSIÇÃO A VALIDAR` ou `[LACUNA]` resolvida deve virar decisão explícita aqui, removendo a marca quando confirmada (registrando no changelog).

---

## Changelog

> Movido para [`docs/CHANGELOG.md`](docs/CHANGELOG.md) (Sprint 2 / item 2.7, B4) — a tabela
> datada de 24 entradas inflava este documento a cada entrega. **Registrar novas entradas
> lá**, linha mais nova no topo. Decisões arquiteturais grandes (não incrementos de feature)
> viram um ADR em [`docs/adr/`](docs/adr/README.md) em vez de só uma linha de changelog.

---

## Tabela de Estado de Implementação

| Feature | Status | Fase | Observações |
|---|---|---|---|
| Setup FastAPI + Dockerfile (porta 8080) | ✅ Implementado | 1 | Scaffolding `app/`, Dockerfile multi-stage (Tailwind CLI + libmagic), deps fixadas. Deploy Railway pendente. |
| `GET /health` | ✅ Implementado | 1 | `/health` (liveness) + `/health/ready` (pool). Testado. |
| Migrations base (enums + 7 tabelas + índices) | ✅ Implementado | 1–2 | Schema canônico Seção 5. Migrations `0001_init_enums` + `0002_tables_core` aplicadas no projeto `iurlzlhbnoemkzgexcfk` e versionadas em `supabase/migrations/`. RLS habilitado na Fase 2 (`0004`). |
| Triggers (`trigger_set_timestamp`, `gerar_codigo_chamado`, `calcular_sla_chamado`, `handle_new_user`) | ✅ Implementado | 2 | Migration `0003_triggers`. Código BOND com contador anual atômico; SLA com escada C1; smoke test verde. |
| Auth (login/logout/refresh) + cookies de sessão | ✅ Implementado + **validado e2e live** | 2 | Login/logout + cookies httpOnly+Secure+SameSite. **🔁 Signup público removido** — cadastro direto no Supabase (`supabase/registro_usuarios.sql`, dual-write perfis+`app_metadata`). **Refresh de sessão** (renova access via refresh + `SessionRefreshMiddleware`); 401 de navegação → `/login`. Login de TI/RH/CLIENTE validado ao vivo (JWT ES256/JWKS). |
| Verificação JWT (JWKS/HS256) | ✅ Implementado | 2 | PyJWT, JWKS assimétrico + fallback HS256; testado (HS256). Confirmar modo do projeto live. |
| RLS + policies por papel | ✅ Implementado | 2–3 | `0004`/`0005` (base) + **🔁 `0008`/`0009` (roteamento por departamento)**. 8 tabelas; helpers `auth_role`/`auth_empresa_id`/`auth_departamento_id`; `anon` sem acesso. |
| **🔁 Roteamento por departamento** | ✅ Implementado + **validado e2e** | 3 | `0008` + `0010` (RH/Mkt = setor + próprios; funcionário = próprios) + **`0020`** (TI deixa de ter acesso total de leitura/escrita — vira igual a RH/Mkt, restrito ao próprio setor; mantém gestão de catálogos e o repasse). Validado contra Supabase live (simulação de claims, 2026-07-01). Portal com seletor; helper `auth_is_ti()`. |
| **🔁 Departamento Químico — abertura dinâmica por categoria + resumo por IA** | ✅ Implementado + **migration aplicada em produção** | 3/5 | Migration `0049` **aplicada no Supabase de produção** (2026-07-22). "Dpto Químico" (`0027`) passa a `recebe_chamados=true` + 3 categorias (Registro de Ocorrência / Visita Técnica / Análise Laboratorial), cada uma com o **layout de campos real dos 3 Microsoft Forms do setor** (schema em código: `app/domain/formularios_quimico.py` — 23 campos, incluindo `min_chars`, `email`/`tel`, `checkbox_multi`) trocado via cascata HTMX (`GET /portal/chamados/campos` → `portal/_campos_quimico.html`, container em `novo_chamado.html`, toggle em `novo_chamado.js`). Respostas em `chamados.dados_formulario` (jsonb — `str` normal, `list[str]` para `checkbox_multi` —, validado no POST contra só as chaves do schema). **Todos os dropdowns do form original têm a lista real** (anexada pelo usuário 2026-07-22): Região (114 opções + "VENDA DIRETA"), Supervisor (24), Gerente (4), Tipo de Ocorrência (3), Produto (66, catálogo Bondmann), e "Região do cliente" (Visita Técnica) reaproveita a mesma lista de Região — nenhum campo ficou como texto livre. **Tela de abertura simplificada pro Químico** (`novo_chamado.js`, mesmo padrão do toggle Marketing): rótulo "Categoria" vira **"Formulários"**; **Subcategoria** escondida (nenhuma categoria do setor tem — ficaria parada em "Escolha a categoria primeiro"); **Assunto/Prioridade/Descrição detalhada** também escondidos — o layout dinâmico já cobre tudo. Como Assunto/Descrição são obrigatórios no servidor, `app/domain/formularios_quimico.py::titulo_e_descricao_automaticos()` deriva os dois das respostas do formulário (ex.: `"Registro de Ocorrência — {cliente}"` + a narrativa da categoria) em `app/routes/portal.py::criar_chamado`, ANTES da checagem de obrigatoriedade; Prioridade mantém o padrão `MEDIA` (select escondido, não removido). **IA (Groq, plugável, free tier):** `app/services/ia_resumo.py` resume o chamado como **nota interna** (`chamados.resumo_ia`), rodada como `BackgroundTask` do Starlette anexada ao redirect (não bloqueia, tolerante a falha, desligada sem `GROQ_API_KEY`), gravada via `admin_connection()`; exibida só ao staff (`workspace/atendimento.html`). RLS reutiliza a genérica por `departamento_id` (`0008`) — sem policy nova. Testes: `test_formularios_quimico`/`test_ia_resumo` + fluxo em `test_portal` (incluindo `checkbox_multi` e abertura sem Assunto/Descrição preenchidos); suíte completa (81 casos entre os dois arquivos + fluxo Químico) verde via `.venv/Scripts/python.exe -m pytest`. `GROQ_API_KEY` configurada pelo usuário no `.env`. |
| **Frente de IA de triagem (TI + Químico) — F0–F2 + avaliação 1–5 ★** | ✅ F0 concluída · ✅ F1/F2 em código (2026-07-23) — sombra no Railway pendente | frente própria | Governada pelo [`plano_md_mestre_IA.md`](plano_md_mestre_IA.md) (detalhe lá — não duplicar aqui). F0: migrations `0050_ia_triagens` e `0051_ia_triagens_avaliacao` **aplicadas em produção**, flags `IA_TRIAGEM_*`, `app/ia/cliente.py`, perfil "Assistente IA" criado/promovido em produção. F1: motor de triagem (`app/ia/triagem.py`, nota interna assinada, custo auditável) — validação em sombra pendente de envs no Railway. F2: perguntas públicas + e-mail + re-triagem (portal e inbound), teto de 2 rodadas — atrás de `IA_TRIAGEM_MODO_SOMBRA=false`. **Avaliação da nota interna (1–5 ★)** pelo staff no Workspace alimenta o KPI "notas úteis ≥ 70%" (`ia_triagens.avaliacao`). **F3 (busca de semelhantes, FTS português)** entregue em 2026-07-23: migration `0053_chamados_fts` em produção + `app/repositories/ia_busca.py` + casos citados na nota interna. **Sombra ativa em produção** (envs no Railway, 2026-07-23). Próximas fases: F4 (agente Químico) → F5 (red team) → F6 (go-live). |
| **🔁 Setor unificado com Departamento + `recebe_chamados`** | ✅ Implementado | 3/5 | Migration `0027`. `departamentos` vira catálogo único (antes "Setor" era lista Python hardcoded, duplicada da tabela). Coluna `recebe_chamados` marca quem tem fila (TI/RH/Marketing) e pode ser destino de chamado; os outros 7 setores só identificam quem abriu. Trigger de guarda-corpo no destino do chamado. Admin (`/admin/gestao`) ganha toggle por setor. Aplicada em produção; 78 testes verdes (`test_portal`/`test_workspace`/`test_admin`). |
| **🔁 Líder de setor (acompanha chamados da equipe)** | ✅ Implementado | 3–5 | Migration `0028`. Todo cadastro (`/admin/usuarios`) passa a exigir setor de origem, inclusive Funcionário/CLIENTE. `ADMIN` com `departamento_id`, mesmo num setor sem fila (ex.: Comercial), **vê** (RLS, só leitura) chamados abertos pela própria equipe fora do seu setor de destino — via `/admin` (KPIs já escopados por RLS) e `/workspace` (fila/kanban/atendimento). Quem atende continua sendo só o staff do setor de **destino** do chamado (TI incluído — corrigido nesta sessão um bug que dava bypass incondicional ao TI, contradizendo a `0020`); `atendimento.html` esconde as ações fora desse escopo. Aplicada em produção; validado ao vivo com dados reais + 86 testes verdes. ⚠️ Kanban ainda permite tentar arrastar/excluir card fora do próprio setor (RLS bloqueia a escrita; UI não impede a tentativa). |
| **🔁 Cadastro direto no Supabase (sem signup)** | ✅ Preparado | 2–3 | `handle_new_user` vincula à org interna; `supabase/registro_usuarios.sql` promove papel/departamento. `/cadastro` removido. |
| CSRF + Security headers + CSP estrita | ✅ Implementado | 2 | Double-submit + middleware de headers/CSP. Testado. Alpine/HTMX self-hosted pendente (Fase 3). |
| Camada de dados asyncpg + `SET LOCAL` (RLS sob pooling) | ✅ Implementado | 2 | `app/db.py`: transaction mode, `statement_cache_size=0`, claims por transação. |
| Rate limiting (slowapi) — backend | ✅ Implementado | 2 | Aplicado em /login (5/min); IP real via X-Forwarded-For. |
| Observabilidade (logs JSON + request-id) | ✅ Implementado | 1 | Middleware de contexto + formatter JSON. **🔁 ASGI puro (Sprint 2 / item 2.3, M5):** `RequestContextMiddleware` (junto com `SecurityHeadersMiddleware` e `SessionRefreshMiddleware`) reescrito sem `BaseHTTPMiddleware` — intercepta só `http.response.start`; `request_id`/`refreshed_session` em `scope["state"]`. ~18% mais rápido no benchmark de `/workspace/fila/fragmento` (mean 5.29ms→4.32ms). |
| Teste de isolamento multi-tenant | ✅ **Validado e2e live + suíte automatizada** | 2 | Simulação de claims (`SET LOCAL ROLE` + `set_config`) contra o Supabase live: RH só vê o setor RH; TI vê tudo; autor CLIENTE não recebe nota interna. Também validado pelo fluxo e2e completo (abertura→chat→pausa→resolução→avaliação→relatório TI). **🔁 Automatizado (Sprint 1 / item 1.7, M9):** `tests/e2e/` (marker `@pytest.mark.rls`) roda a mesma técnica contra Supabase **local** (não mais só validação manual ad-hoc contra produção) — matriz cobre autor, staff RH/Marketing, líder de setor (`0028`), TI pós-`0020`, autoatendimento Marketing/RH (`0038`/`0042`), nota interna, avatar (`0037`). CI dedicado (`.github/workflows/e2e-rls.yml`, só em PRs que tocam `supabase/`/`app/repositories/`). |
| Portal do Cliente — dashboard + abertura + detalhe | 🟡 Em produção | 3 | Rotas `/portal` (FastAPI/Jinja2), `asyncpg`+RLS, tokens da marca. Anexos ✅; abertura com **departamento de destino** ✅; falta: chat Realtime. |
| **Avaliação do chamado (CSAT 1–5 + comentário)** | ✅ Implementado | 3/5 | Migration `0006`; só autor + `RESOLVIDO`; widget de estrelas + caixa de comentário (**opcional acima de 4★, obrigatório com ≥50 caracteres em nota ≤4★** — `validar_comentario_avaliacao`, 2026-07-24) no detalhe. **Feedback nos relatórios do TI:** coluna `avaliacao_comentario` no CSV + painel "Últimas avaliações" no dashboard. Fluxo validado e2e. |
| **🔁 Reabertura do chamado pelo autor** | ✅ Implementado | 3/5 | Migration `0059` (RLS `chamados_update_cliente_reabertura` + trigger, ainda não aplicada em produção). Autor de um `RESOLVIDO` insatisfeito com a solução reabre para `EM_ATENDIMENTO` (`POST /portal/chamados/{id}/reabrir`) — zera avaliação anterior, reabre o chat, registra `REABERTO` no histórico. `PortalService.pode_reabrir` vale mesmo em autoatendimento (não é CSAT). Ver `docs/CHANGELOG.md` 2026-07-24. |
| **Telefone de contato obrigatório na abertura** | ✅ Implementado | 3 | Migration `0058` (`chamados.telefone_contato text NOT NULL`, ainda não aplicada em produção). `validar_telefone_contato` (mín. 8 dígitos) barra a abertura sem o campo; exibido ao autor e ao staff (`workspace/atendimento.html`) — é o motivo de existir: contato direto fora do chat. **🔁 Guardado no perfil (2026-07-29, migrations `0062` + `0063` de backfill, ambas **aplicadas em produção**):** `perfis.telefone` pré-preenche o campo da abertura para o número não ser redigitado a cada chamado; quem não tem cadastro informa na abertura e o número é salvo no perfil (`criar_chamado`, sem derrubar a abertura em caso de falha). **🔁 Revisto em 2026-07-29 (pedido do gestor):** o telefone da abertura atualiza o do perfil **sempre** que for diferente, não só na primeira vez — quem trocou de celular corrige uma vez, na abertura, e o cadastro acompanha (antes exigia uma segunda edição em `/perfil`, que ninguém fazia). `chamados.telefone_contato` continua sendo o histórico daquele chamado, `perfis.telefone` o número atual; editar o perfil (`POST /perfil/telefone`) não reescreve chamados antigos. A `0062` também reescreve o trigger `enforce_perfil_self_so_avatar` (`0033`) para que a coluna nova não fique alcançável por quem a `0052` deixa editar o perfil de terceiros; a `0063` faz o backfill a partir do chamado mais recente de cada autor (14 de 152 perfis — o resto nunca abriu chamado com telefone). Ver `docs/CHANGELOG.md` 2026-07-24 e 2026-07-29. |
| Abertura por Categoria + Assunto (sem "Produto") | ✅ Implementado | 3 | Decisão de produto; campo "Produto relacionado" removido (produção + protótipo). |
| **Subcategorias + anexos na abertura + campos obrigatórios** | ✅ Implementado | 2 | Migration `0015_subcategorias` (tabela + `chamados.subcategoria_id` + seed + RLS TI; trava de coluna do CLIENTE estendida). Cascade HTMX categoria→subcategoria; abertura exige todos os campos e aceita anexos multipart (validados antes de criar, viram 1ª mensagem). Testes em `test_portal.py`. ⚠️ pytest a rodar via `.bat`/CI (sessão sem Python). |
| Vendoring HTMX 2.0 + Alpine CSP + supabase-js (self-host) | ✅ Implementado | 3 | `app/static/vendor/` com SRI (HTMX 2.0.4, Alpine CSP 3.14.8, supabase-js 2.47.10) via npm; `script-src 'self'`. Carregados no `app_base` do portal. |
| Storage privado + signed URLs (TTL 1h) | ✅ Implementado | 3 | Migration `0007_storage_anexos` (bucket privado + RLS path-scoped). `app/security/uploads.py` (10MB, allow-list, magic bytes, sanitização) + `app/storage.py` (REST via httpx com JWT do usuário; signed URL TTL 1h on-demand). Upload multipart no detalhe; 45 testes verdes. Validação e2e contra Supabase live pendente. |
| Workspace Operador (Kanban/Lista + SLA visual) | ✅ Implementado | 4 | `/workspace`: fila Lista (polling 15s) + Kanban (Sortable.js DnD → HTMX/fetch persiste status) com **indicador de SLA por cor** (`app/domain/sla_visual.py`, verde/amarelo<25%/vermelho<10%-vencido piscante). Tela de atendimento com ações rápidas. Testado (unit + rotas). **🔁 Colunas extras por setor:** Marketing tem `A_FAZER`/`AGUARDANDO_TERCEIROS` (migrations `0024`/`0043`, exclusivas desde `0048`); **TI tem `PROJETOS`** ("Projetos", migration `0057`, 2026-07-24, **aplicada em produção**), status ativo/não-pausado para demanda de projeto sem vínculo de atendimento reativo; **TI e RH têm `RESPOSTA_CLIENTE`** ("Última Interação do Usuário", migrations `0060`/`0061`, 2026-07-24, ainda **não aplicadas em produção**) — automática via trigger em `mensagens`: entra quando a última mensagem pública é de quem abriu o chamado, sai (volta a `EM_ATENDIMENTO`) quando o setor responde. `app/routes/workspace.py::_status_ui()` ramifica por setor; ver `docs/CHANGELOG.md`. |
| **🔁 SLA próprio da coluna "Projetos" — configurável pelo TI** | ✅ Implementado · `0064` **aplicada em produção**, `0066` **pendente de aplicação** | 4 | Migration `0064` (2026-07-29, **aplicada em produção**): entrar em `PROJETOS` passa a dar **1 mês** de `limite_resolucao` (22 dias úteis, `sla_prazo_projeto()`), em vez das 24h úteis do plano da empresa. Trigger `sla_projetos_um_mes` + ramo de `PROJETOS` na `calcular_sla_chamado` (para a troca de prioridade não derrubar o mês); backfill dos projetos abertos na própria migration. **🔁 Migration `0066` (2026-07-30, pedido do gestor):** o mês fixo vira **prazo configurável** — `chamados.prazo_projeto_dias` por projeto (Workspace → Ações → "Prazo do projeto", em dias corridos: 60 = 2 meses, 70, 120 = 4 meses…) com fallback em `planos_sla.projeto_dias` (padrão do setor, editável em `/admin/gestao`, default 30 = o mesmo mês da `0064`). Base do prazo materializada em `chamados.projeto_em` (a entrada na coluna), para que trocar os dias ajuste a data final sem recontar de hoje; trigger renomeado para `sla_projetos_prazo` (mesma posição na ordem alfabética, depois de `sla_pausa_aguardando`); as duas colunas novas entram na trava `enforce_cliente_so_avaliacao`. Ver Seção 5.2. 9 testes e2e (`tests/e2e/test_sla_projetos.py`) + 4 de rota em `tests/test_workspace.py` + 2 em `tests/test_admin.py`. |
| **🔁 Combinação de chamados duplicados** | ✅ Implementado + **migration aplicada em produção** (2026-07-30) | 4/5 | Migration `0065` (2026-07-30, pedido do gestor): incidente que gera N chamados iguais passa a ser atendido como **um só**. Modelagem, decisões e o ponto de segurança da trava de coluna do CLIENTE estão na **Seção 5.1, item 9** — não duplicar aqui. Superfície: `POST /workspace/chamados/{id}/combinar` (multi-seleção), `/descombinar` (desfaz, restaurando o status anterior lido do histórico `COMBINADO`) e o fragmento HTMX `/combinar/candidatos` (candidatos do mesmo setor **ordenados por semelhança com o chamado atual** via FTS `0053` — no caso "servidor caiu" os repetidos já vêm no topo sem ninguém digitar). Painel só aparece com o chamado **assumido** (`pode_atender`), que é também o que a policy `mensagens_insert` (`0042`) exige para publicar a digest. Toda a operação roda **sob RLS numa transação só** (marcar duplicado + mover a cópia + publicar a digest), sem `admin_connection`. Histórico: `COMBINADO` / `COMBINACAO_RECEBIDA` / `COMBINACAO_DESFEITA`. Testes: `tests/test_combinacao.py` (digest pura, incl. o contrato com `paragrafos_mensagem`), 7 casos de rota em `tests/test_workspace.py`, 2 no `tests/test_portal.py` e `tests/e2e/test_rls_combinacao.py` (guarda-corpos do trigger + o CLIENTE não escapar pela coluna nova). Suíte: 606 verdes. **Aplicada em produção em 2026-07-30**, com conferência prévia do estado real do banco — foi ela que pegou a armadilha das views (ver `docs/CHANGELOG.md`). **CI/CD verde no commit `7441efb`** (2026-07-30): workflow `CI` ✅ (pytest + piso de cobertura — 76.99% contra piso de 69%, build Tailwind, build da imagem Docker, numeração de migrations, **migrations aplicadas** — este último só passa porque a `0065` foi ao banco ANTES do push, que é a ordem que o job existe para cobrar —, `ruff` e `mypy`) e workflow `E2E RLS` ✅ (35 casos contra Supabase local real, incluindo os 6 novos de `test_rls_combinacao.py`, que rodaram pela primeira vez ali: sem Docker na máquina de desenvolvimento, o CI é o único lugar onde os guarda-corpos do trigger são de fato exercitados). |
| **🔁 TMA de "Projetos" separado nos indicadores do Admin** | ✅ Implementado | 5 | `AdminRepo.kpis()` (2026-07-24, pedido do usuário): TMA geral **exclui** chamados resolvidos diretamente a partir da coluna Projetos (identificado via `historico_chamados`, sem coluna nova); `tma_projetos_horas`/`projetos_resolvidos` como métricas próprias, cards dedicados no dashboard só pro TI. Ver `docs/CHANGELOG.md`. |
| Ações rápidas + histórico (status/prioridade/atribuição) | ✅ Implementado | 4 | Cada mudança grava `historico_chamados`; prioridade recalcula SLA (trigger); `respondido_em` na 1ª resposta pública. Escopo por RLS (TI tudo, RH/Mkt seu setor). |
| **Iniciar atendimento + barra de SLA + repasse por TI** | ✅ Implementado | 3 | Botão "Iniciar atendimento" (NOVO→EM_ATENDIMENTO + assume). **Barra de progresso** de SLA (`barra_sla`, verde→amarelo na metade→vermelho; largura via CSSOM CSP-safe). Responsável atribuível **só do setor do chamado**; **TI repassa** para outro departamento (`transferir`; RLS já restringe ao TI, sem migration). Testes verdes. |
| Notas internas (`is_interna`) | ✅ Implementado | 4 | Toggle no composer (fundo amarelo via `workspace.js`); `is_interna` decidido no servidor; RLS+Realtime não entregam ao solicitante (validado e2e na `0010`). |
| Chat Realtime (supabase-js direto) | ✅ Implementado | 3 | `0011` (publicação Realtime em `mensagens`) + `chat.js` (assina `postgres_changes` por `chamado_id`, JWT do usuário, RLS na entrega) → refresh do fragmento HTMX; **fallback polling 10s**. Usado no portal **e** no workspace. **🔁 2026-07-17:** miniatura clicável 64×64 pra anexos de imagem (mime já validado no upload) + `paragrafos_mensagem()` (`app/templating.py`) rejunta texto colado com quebra manual e reformata na largura real do balão, preservando listas/tópicos linha a linha; coluna de conversa em `atendimento.html` ganhou mais espaço (2/3→3/4) frente ao painel de Ações. Ver `docs/CHANGELOG.md`. |
| Painel Admin + KPIs + Export CSV | ✅ Implementado | 5 | `/admin`: KPIs (total/abertos/resolvidos, **conformidade de SLA**, **CSAT**, **TMA**), gráficos **Chart.js** (status, CSAT 1–5, por departamento, produtividade por operador), **painel "Últimas avaliações"** (feedback qualitativo), **gestão** de departamentos/categorias (criar/ativar-desativar, **só TI**) + planos (leitura), e **`GET /admin/export/csv`** (inclui `avaliacao_comentario`). Testado + fluxo e2e ao vivo. **🔁 Âncora do CSAT (2026-08-03, pedido do usuário):** o CSAT do mês é lido sobre os chamados **resolvidos no mês** (`resolvido_em`), não sobre os avaliados no mês (`avaliacao_em`) — a nota é do atendimento entregue naquele mês, então mês fechado para de mudar quando chega avaliação atrasada. Vale para o KPI, o gráfico de distribuição, o modal de cada nota e "Últimas avaliações" (mesma base nos quatro); `csat_respostas` vira a taxa de resposta sobre `resolvidos`. Ver `docs/CHANGELOG.md` 2026-08-03. |
| **Admin de departamento (papéis por setor)** | ✅ Implementado | 4 | `/admin` agora entra p/ **TI** (tudo + gestão) **e ADMIN de setor** (role `ADMIN`+`departamento_id`, ex.: gestor RH) — vê indicadores **só do seu setor** (CSAT/SLA/rapidez/avaliações; RLS escopa). OPERADOR/CLIENTE = 403. Gestão de catálogos só TI. `registro_usuarios.sql` documenta OPERADOR×ADMIN + exemplo de gestor RH. Testado (`test_admin`). |
| **Sino de notificações em tempo real** | ✅ Implementado + **validado ao vivo** | 4 | `notificacoes.js` assina Realtime de `chamados`+`mensagens` (RLS na entrega) e acende/toca o sino + recarrega a lista a cada mudança significativa. Migration `0016_realtime_chamados` (publicação = `chamados, mensagens`). Rota `/realtime/config` (JWT do usuário). Fluxo depto→visibilidade revalidado (RH só vê RH). |
| **Redefinição de senha (OTP por e-mail)** | ✅ Implementado | 5 | `/esqueci-senha`→`reset_password_for_email`; `/redefinir-senha`→`verify_otp` (recovery) + `update_user`; cliente Supabase isolado por request; rate limit 5/min; link no login. ⚠️ requer template de e-mail com `{{ .Token }}`. Testado (validação). |
| Política de senha (mínimo 8, fonte única) | ✅ Implementado | Sprint 2 / 2.8 | `app/security/password_policy.py::SENHA_MIN_CHARS` (Seção 3.4.1) — antes duplicada em `app/auth/routes.py`/`app/routes/admin.py`. `[AÇÃO DO GESTOR PENDENTE]`: mesmo mínimo no painel Supabase (hoje default 6). |
| MFA (staff/ADMIN) | 🟡 Avaliado, não implementado | Sprint 2 / 2.8 | Ver Seção 3.4.1 — faseamento recomendado (TOTP opcional pro staff na Fase 1, alvo Sprint 3; obrigatório pro ADMIN na Fase 2, alvo Sprint 4). |
| **Páginas de erro 403/404** | ✅ Implementado | 5 | `erro.html` via handler central (registrado na `StarletteHTTPException`); só em navegação HTML. Testado. |
| **Animações fluidas** | ✅ Implementado | 5 | `app/static/css/anim.css` (entrada de página/cartões, stagger, sino, barra de SLA suave, `prefers-reduced-motion`). CSS estático CSP-safe, sem rebuild do Tailwind. |
| **Teste de carga (uso em grande escala)** | ✅ Scripts prontos | 5 | `tests/load/locustfile.py` (100 CCU, rotas autenticadas, taxa de 304) + `tests/load/smoke_carga.py` (rajada bruta + percentis). Rodar contra ambiente de teste (nunca produção). |
| Deploy (Railway, alvo único) | ✅ Decidido e único alvo | Sprint 1 / 1.8 | **`[DECISÃO DO GESTOR]` registrada 2026-07-15 (auditoria, item M10):** Railway (Dockerfile, processo persistente) é o único alvo de produção — servidor persistente cabe melhor à stack (asyncpg com pool real, libmagic, `BackgroundTasks` com garantia de conclusão pós-response). O deploy na Vercel (serverless, ficou no ar entre 2026-07-01 e 2026-07-15 — ver changelog) foi **desativado**: `vercel.json`/`.vercelignore` removidos, `Settings.is_serverless` e os ramos condicionais em `app/db.py::init_pool` e `app/notification.py::agendar_notificacao_email` (modo inline p/ contornar `BackgroundTasks` morrendo pós-response no serverless) eliminados — `BackgroundTasks` sempre assíncrono agora, sem modo alternativo. |
| **Foto de perfil (avatar)** | ✅ Implementado + **validado ao vivo** (3 causas raiz corrigidas) | 7 | `/perfil` (qualquer autenticado) + upload opcional na criação de conta (`/admin/usuarios`, só TI). Bucket público `avatares` (migration `0033`), path fixo `{user_id}/avatar.png` (reenvio substitui). **Fix 1 (app):** o upload travava indefinidamente em dev Windows (`import magic` do `python-magic-bin` trava dentro do próprio import — não é exceção, o `try/except` não pegava); agora com sonda de disponibilidade com timeout (`app/security/uploads.py`). **Fix 2 (banco — migration `0037`):** a `0035` tinha removido `avatares_select`; sem policy de SELECT, Postgres não deixa o próprio dono dar UPDATE na linha (reenvio roda como UPDATE, não INSERT) — 1º upload sempre funcionava, reenvio sempre falhava. Nova policy `avatares_select_own` (restrita ao próprio avatar) resolve; confirmado ao vivo antes/depois (simulação de claims em transação com rollback). **Fix 3 (CSP — `app/security/headers.py`):** upload OK mas a imagem aparecia quebrada — `img-src` da CSP era `'self' data:`, nunca liberou o host do Supabase; avatar é o 1º `<img src>` cross-origin embutido inline (anexos são link/signed URL, não imagem embutida). `img-src` passa a incluir `settings.supabase_url` (mesmo host já confiável do `connect-src`; bucket público, sem exposição de segredo). **Recorte automático:** `app/avatar_storage.py::preparar_avatar` (Pillow) recorta em quadrado centralizado + redimensiona 512×512 + normaliza para PNG, qualquer que seja a proporção/formato (jpg/png) enviado — some com o `rounded-full object-cover` do CSS pra exibir como bolinha em todo card (fila/kanban/detalhe). Testes (`test_perfil.py`) decodificam o PNG salvo e confirmam largura=altura; `get_advisors` sem novos achados. |
| SLA — validação com gestor | Planejado | pré-4 | C1 / Seção 5.2. |
| Cache tenant-scoped (categorias/planos) | ✅ Implementado | 2–3 | `app/cache.py` (TTL 90s por-processo, chave global pois catálogos são globais/org única), invalidado na escrita do admin. Redis se >1 réplica. |
| Rate limiting (slowapi) | ✅ Implementado | pré-deploy | `app/ratelimit.py`: `/login` (5/min) + abertura de chamado (15/min); IP real via X-Forwarded-For. In-memory; Redis se >1 réplica. |
| ETag/304 no polling da fila | ✅ Implementado | perf | `/workspace/fila/fragmento`: assinatura leve (count+max updated_at) → 304 quando nada muda. |
| Performance: 1 conexão RLS/request + GZip | ✅ Implementado | perf | Holder lazy por request (contextvar) reusa a conexão; `SET LOCAL ROLE`+`set_config` em 1 round-trip; GZip. Piso restante = latência ao banco remoto (~320ms/query); fluidez local plena exige DB local. |
| Observabilidade (Sentry + métricas mínimas) | ✅ Implementado | Sprint 2 / 2.6 | Logs JSON + request-id já cobertos na linha acima (Fase 1). **Sentry** (`app/observability.py::configure_sentry`) opcional via `SENTRY_DSN` vazia = desligado; exceção não tratada capturada em `app/main.py::_unhandled_exception_handler` com a tag `request_id` (`sentry_sdk.Scope()` isolado por chamada — sem vazar entre requests concorrentes). **`GET /metrics`** (`app/metrics.py`, mesmo gate de token de `/health/ready`): contagem de status/5xx, p95 de latência por rota (janela de 500 amostras), taxa de 304 do polling da fila, saturação do pool asyncpg (`app/db.py::pool_stats`). Critério de go-live "48h sem 5xx" agora verificável em `/metrics` sem grep manual de log. **Uptime check externo pendente** — `[DECISÃO/AÇÃO DO GESTOR]`, ver item 2.6 do plano de melhorias (assinatura de serviço terceiro, fora do alcance de um PR). |

---

### Pendências de validação consolidadas (`⚠️`)

- ~~**SLA:** unidade (corridas vs comercial/feriados), pausa (`AGUARDANDO`)~~ ✅ **RESOLVIDO 2026-07-05** (gestor): horário comercial seg–sex 08–18, para em feriados, `AGUARDANDO` pausa — migration `0017`. Escada de fallback (C1) mantida.
- **Prioridades:** confirmar conjunto `BAIXA/MEDIA/ALTA/URGENTE`.
- **Perfis:** `empresa_id` de OPERADOR/ADMIN (NULL vs empresa matriz).
- **Categorias:** globais vs por-tenant (adotado global).
- **JWT:** JWKS assimétrico vs HS256 legado (confirmar no projeto Supabase).
- **Realtime + RLS:** confirmar que a entrega respeita `is_interna` e CSP `connect-src wss`.
- **Versões `⚠️ A CONFIRMAR`:** travar patches exatos no lockfile no setup (Seção 0).
- **Relatórios para OPERADOR:** confirmar se só ADMIN acessa.
- **Combinação de chamados (`0065`):** quem entra "em cópia" recebe atualização por **sino/Realtime**, não por **e-mail** — `notificar_nova_mensagem_email` endereça só autor/operador, e o reply-to inbound é assinado por usuário (um observador respondendo por e-mail esbarraria na `mensagens_insert`). ⚠️ **VALIDAR COM O GESTOR** se o e-mail para observadores é necessário; se for, é trabalho próprio (destinatários múltiplos + o que fazer com o inbound), não um ajuste desta entrega.
- **CSAT:** depende de e-mail transacional (pode ir a backlog).

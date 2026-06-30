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
- **IP real atrás do proxy Railway:** extrair via **`X-Forwarded-For`** (primeiro IP confiável), com `ProxyHeaders`/`--proxy-headers` no Uvicorn e `forwarded_allow_ips` apropriado. Não usar o IP da conexão direta (será o do proxy).
- **Isenção da rota de polling** da fila (senão usuários legítimos são bloqueados pelos ~20 req/s agregados).
- **`[LACUNA — DECIDIR]` storage do limiter:**
  - **DECISÃO MVP:** **in-memory por réplica** (default do slowapi) enquanto rodarmos **1 réplica**.
  - **Com múltiplas réplicas:** migrar para **Redis** (storage compartilhado) — caso contrário o limite efetivo é multiplicado pelo nº de réplicas. Mesmo gatilho do cache (2.3).

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

Matriz consolidada (das Seções 4.3 e 5 da spec):

| Recurso / Ação | CLIENTE | OPERADOR | ADMIN |
|---|---|---|---|
| Ver chamados | Só da **própria empresa** | **Todos** | Todos |
| Criar chamado | Sim, com `cliente_id = auth.uid()` e `empresa_id` = sua | Sim | Sim |
| Atualizar `status` / `prioridade` / `operador_id` | **Não** | **Sim** | Sim |
| Ver mensagens `is_interna = true` | **Não** | Sim | Sim |
| Criar mensagem pública | Sim (nos seus chamados) | Sim | Sim |
| Criar nota interna (`is_interna = true`) | **Não** | Sim | Sim |
| **Avaliar chamado (1–5 ★, CSAT)** | **Só o autor**, e só quando `RESOLVIDO` | Não | Não |
| Ver histórico/auditoria do chamado | Da própria empresa | Todos | Todos |
| Gerir Planos de SLA | Não | Não | **Sim** |
| Gerir Empresas | Não | Não | **Sim** |
| Gerir Categorias | Não | Não | **Sim** |
| Convidar/gerir usuários (Operador/Admin) | Não | Não | **Sim** |
| Relatórios/KPIs e Export CSV | Não | `⚠️ A VALIDAR` (spec dá `/admin` a ADMIN; workspace a OPERADOR+ADMIN) | **Sim** |
| Acesso a Storage de anexos | Só do próprio tenant (path-scoped) | Todos | Todos |

> `⚠️ SUPOSIÇÃO A VALIDAR:` se OPERADOR pode acessar relatórios. A spec coloca `/admin` (relatórios) sob perfil ADMIN e `/workspace` sob OPERADOR+ADMIN — adotado assim acima. Confirmar com o gestor.

### 3.3 Políticas RLS por papel (a implementar como SQL nas migrations)

RLS **habilitado nas 7 tabelas**. Princípio do menor privilégio. Resumo das políticas (DDL completo nas migrations da Fase 2):

- **CLIENTE**
  - `SELECT chamados`: `empresa_id = (perfil do auth.uid()).empresa_id`.
  - `INSERT chamados`: `cliente_id = auth.uid()` **e** `empresa_id` = empresa do perfil.
  - `SELECT mensagens`: do chamado da sua empresa **e** `is_interna = false`.
  - **Sem** `UPDATE` de status/prioridade/operador.
  - **`UPDATE chamados` (avaliação)**: permitido **apenas** no chamado próprio (`cliente_id = auth.uid()`) e **só quando `status = 'RESOLVIDO'`** (policy `chamados_update_cliente_avaliacao`). Como RLS não restringe colunas, o trigger `enforce_cliente_so_avaliacao` (Seção 5.3) garante que o CLIENTE só altere `avaliacao_nota`/`avaliacao_comentario`/`avaliacao_em` — qualquer mudança em status/prioridade/título/etc. é rejeitada. Defesa em profundidade: a rota também checa autor + `RESOLVIDO` antes de gravar.
- **OPERADOR**
  - `SELECT/UPDATE chamados`: todos. `UPDATE` permitido em `status`, `prioridade`, `operador_id`.
  - `SELECT/INSERT mensagens`: todas, incluindo `is_interna`.
- **ADMIN**
  - Acesso irrestrito a todas as tabelas (incl. `planos_sla`, `empresas`, `categorias`, gestão de `perfis`).

Helper recomendado: função `auth_empresa_id()` (lê o `empresa_id` do `perfil` do `auth.uid()`) e `auth_role()` para uso nas policies, evitando subqueries repetidas.

### 3.4 Sessão e Auth (server-rendered) — `[LACUNA]`, a spec não define

- **Token em cookie `httpOnly` + `Secure` + `SameSite=Lax`** (`Strict` onde não houver navegação cross-site necessária). **Nunca** em `localStorage` nem header `Bearer` no browser.
  - `[DECISÃO]` `SameSite=Lax` como padrão (compatível com navegação normal e POSTs same-site do HTMX); reservar `Strict` para o cookie de refresh, se separado.
- **Conteúdo do cookie:** `[DECISÃO]` armazenar o **access token JWT do Supabase** (curto) no cookie de sessão e o **refresh token** em cookie separado `httpOnly`/`Secure`/`SameSite=Strict`. Alternativa: sessão opaca server-side assinada com `itsdangerous` referenciando os tokens — preferível se quisermos revogação imediata. `⚠️ A VALIDAR` na Fase 2.
- **Refresh:** middleware verifica expiração do access token; se expirado e refresh válido, renova via `supabase-py` (`auth.refresh_session`) e re-seta cookies. Falha → redireciona a `/login`.
- **Logout:** invalida sessão no Supabase (`auth.sign_out`) e **limpa os cookies** (expira no passado). 
- **Expiração:** access token conforme config do Supabase (ex.: 1h); sessão de aplicação expira com o refresh token. Inatividade prolongada → re-login.

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
  - **Allow-list de tipos:** `pdf, jpg, png, mp4, docx, xlsx`.
  - **Validação do MIME real por *magic bytes*** (`python-magic`) — **não** confiar no `Content-Type`/extensão enviados pelo cliente.
  - **Sanitização do nome de arquivo** (remover path traversal, normalizar; preferir nome gerado/UUID + extensão validada).
  - **Idempotência contra duplo-submit** (token de submissão único por upload / dedupe por hash do conteúdo no mesmo chamado).

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

### 5.3 Triggers/Functions (nomes exatos da spec)

1. **`trigger_set_timestamp`** — `BEFORE UPDATE` em `planos_sla`, `empresas`, `perfis`, `chamados`: seta `updated_at = now()`. (`planos_sla` incluída na implementação por também possuir `updated_at`; estende a lista da spec.)
2. **`gerar_codigo_chamado`** — `BEFORE INSERT` em `chamados`: gera `codigo` no formato **`BOND-YYYY-NNNNN`** via **sequence dedicada** (sem `SELECT MAX()+1` — seria *racy*).
3. **`calcular_sla_chamado`** — `BEFORE INSERT` (e em mudança de prioridade) em `chamados`: calcula `limite_resposta`/`limite_resolucao` conforme 5.2 + escada C1.
4. **`handle_new_user`** — `AFTER INSERT` em `auth.users`: cria `perfis` com `role = 'CLIENTE'`.
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

1. **Changelog datado** (tabela abaixo): registrar `data · seção alterada · resumo` a cada mudança no doc.
2. **Tabela "Estado de Implementação"** (abaixo): atualizar `feature · status · fase · observações` **a cada entrega**.
3. **Regra de precedência:** se **schema, RLS ou regra de SLA** mudar, **a Seção correspondente (5 / 3 / 5.2) é atualizada ANTES** de prosseguir com o código. O código segue o doc, não o contrário.
4. Toda nova `⚠️ SUPOSIÇÃO A VALIDAR` ou `[LACUNA]` resolvida deve virar decisão explícita aqui, removendo a marca quando confirmada (registrando no changelog).

---

## Changelog

| Data | Seção alterada | Resumo |
|---|---|---|
| 2026-06-26 | Todas | Criação do plano mestre a partir da spec v2.0. Contradições C1–C3 resolvidas; decisões de RLS×pooling, cache tenant-scoped, CSRF/CSP/headers, Storage, schema canônico das 7 tabelas, código BOND seguro sob concorrência, topologia Realtime e cronograma com DoD por fase. |
| 2026-06-26 | 5.1 / 5.4 / 6.2 / Estado | Início da produção (Fase 1). Aplicadas migrations `0001_init_enums` (3 enums) e `0002_tables_core` (7 tabelas + índices) no projeto Supabase `iurlzlhbnoemkzgexcfk`, materializando o schema canônico da Seção 5.1; `.sql` versionados em `supabase/migrations/`. Adicionada **regra dura de segurança** (6.2): chaves de API nunca expostas em código, docs ou qualquer artefato — só em env vars. RLS ainda **desabilitado** nas 7 tabelas (trabalho da Fase 2). |
| 2026-06-26 | 0.2 / 1 / 2 / 3 / 6 / Estado | Fundação do backend (Fase 1 + Fase 2 backend). Scaffolding FastAPI (`app/`), `requirements.txt` com versões fixadas, Dockerfile multi-stage (Tailwind CLI + Python 3.12 slim + libmagic, porta 8080), Pydantic Settings, **camada de dados `asyncpg` com `SET LOCAL ROLE authenticated` + claims por transação** (Seção 3.1), cliente Supabase async só para Auth, **verificação local de JWT** (PyJWT, JWKS+HS256), **CSRF double-submit**, **security headers + CSP estrita**, cookies de sessão, rate limiting (slowapi em /login e /cadastro), logging JSON com request-id, `/health` + `/health/ready`, rotas `/login` `/logout` `/cadastro` e templates base. Suíte pytest (9 testes: health, CSP, CSRF, JWT) verde; build do Tailwind validado. Adicionado PyJWT à Seção 0.2. |
| 2026-06-26 | 3.3 / 5.3 / 5.4 / Estado | Fase 2 (banco). Aplicadas `0003_triggers` (`trigger_set_timestamp`, `gerar_codigo_chamado` com contador anual atômico, `calcular_sla_chamado` com escada C1, `handle_new_user`), `0004_rls_policies` (helpers `auth_role`/`auth_empresa_id` SECURITY DEFINER, **RLS habilitado nas 7 tabelas + `contador_chamados`**, policies por papel da Seção 3.3, grants com `anon` sem acesso e imutabilidade de `mensagens`/`historico`) e `0005_harden_functions` (search_path fixo + REVOKE de EXECUTE nas functions de trigger). Smoke test em transação revertida confirmou código `BOND-YYYY-00001`, SLA URGENTE = 50% de ALTA e perfil CLIENTE automático. Advisor de segurança: 0 erros (resta apenas INFO de `contador_chamados` deny-all, intencional). |
| 2026-06-30 | Consolidação de branches / 3.2 / 3.3 / 5.1 / 5.3 / 6 / Estado | **Consolidação:** backend (branch `supabase-table-setup`) + protótipos (branch `portal-screen-prototypes`) reunidos na branch de trabalho, com **um único MD autoritativo** para todo o projeto (as outras branches tinham cópias desatualizadas — esta passa a ser a fonte de verdade). **Avaliação (CSAT 1–5):** migration `0006_avaliacao_chamado` (colunas `avaliacao_*` em `chamados` + CHECK 1–5, policy `chamados_update_cliente_avaliacao` restrita a autor + `RESOLVIDO`, trigger `enforce_cliente_so_avaliacao` travando colunas, índice CSAT) aplicada no projeto `iurlzlhbnoemkzgexcfk`; advisors sem novos erros. **Decisão de produto:** abertura de chamado é por **Categoria + Assunto** — campo "Produto relacionado" removido do protótipo (`build.py` + `cliente-novo-chamado.html`). **Fase 3 (início):** Portal do Cliente em produção — rotas `/portal` (dashboard, novo chamado, detalhe, mensagens, avaliação), repositório `asyncpg`+RLS, templates Jinja com tokens da marca e widget de estrelas; CSAT deixa de depender de e-mail (Fase 5). Suíte pytest 30 testes verde (9 anteriores + 21 do portal/avaliação). |

---

## Tabela de Estado de Implementação

| Feature | Status | Fase | Observações |
|---|---|---|---|
| Setup FastAPI + Dockerfile (porta 8080) | ✅ Implementado | 1 | Scaffolding `app/`, Dockerfile multi-stage (Tailwind CLI + libmagic), deps fixadas. Deploy Railway pendente. |
| `GET /health` | ✅ Implementado | 1 | `/health` (liveness) + `/health/ready` (pool). Testado. |
| Migrations base (enums + 7 tabelas + índices) | ✅ Implementado | 1–2 | Schema canônico Seção 5. Migrations `0001_init_enums` + `0002_tables_core` aplicadas no projeto `iurlzlhbnoemkzgexcfk` e versionadas em `supabase/migrations/`. RLS habilitado na Fase 2 (`0004`). |
| Triggers (`trigger_set_timestamp`, `gerar_codigo_chamado`, `calcular_sla_chamado`, `handle_new_user`) | ✅ Implementado | 2 | Migration `0003_triggers`. Código BOND com contador anual atômico; SLA com escada C1; smoke test verde. |
| Auth (login/logout/cadastro) + cookies de sessão | 🟡 Backend pronto | 2 | Rotas + cookies httpOnly+Secure+SameSite implementados. Falta validação e2e contra Supabase live e fluxo de refresh. |
| Verificação JWT (JWKS/HS256) | ✅ Implementado | 2 | PyJWT, JWKS assimétrico + fallback HS256; testado (HS256). Confirmar modo do projeto live. |
| RLS nas 7 tabelas + policies por papel | ✅ Implementado | 2 | Migrations `0004_rls_policies` + `0005_harden_functions`. Helpers `auth_role`/`auth_empresa_id`; `anon` sem acesso. Acesso de domínio via asyncpg + `SET LOCAL` (a implementar no backend). |
| CSRF + Security headers + CSP estrita | ✅ Implementado | 2 | Double-submit + middleware de headers/CSP. Testado. Alpine/HTMX self-hosted pendente (Fase 3). |
| Camada de dados asyncpg + `SET LOCAL` (RLS sob pooling) | ✅ Implementado | 2 | `app/db.py`: transaction mode, `statement_cache_size=0`, claims por transação. |
| Rate limiting (slowapi) — backend | ✅ Implementado | 2 | Aplicado em /login (5/min) e /cadastro (3/min); IP real via X-Forwarded-For. |
| Observabilidade (logs JSON + request-id) | ✅ Implementado | 1 | Middleware de contexto + formatter JSON. |
| Teste de isolamento multi-tenant | Planejado | 2 | **Bloqueia** validação final da Fase 3 (RLS real contra Supabase local). |
| Portal do Cliente — dashboard + abertura + detalhe | 🟡 Em produção | 3 | Rotas `/portal` (FastAPI/Jinja2), repositório `asyncpg`+RLS, templates com tokens da marca. Falta: chat Realtime e upload de anexos. |
| **Avaliação do chamado (CSAT 1–5)** | ✅ Implementado | 3 | Migration `0006`; só autor + `RESOLVIDO`; widget de estrelas no detalhe; 30 testes verdes. |
| Abertura por Categoria + Assunto (sem "Produto") | ✅ Implementado | 3 | Decisão de produto; campo "Produto relacionado" removido (produção + protótipo). |
| Vendoring HTMX 2.0 + Alpine CSP (self-host) | Planejado | 3 | Rating já funciona sem JS (form PRG) e com HTMX (header `HX-Request`). Faltam os bundles em `/static` com SRI. |
| Storage privado + signed URLs (TTL 1h) | Planejado | 3 | Path `{empresa_id}/{chamado_id}/{arquivo}`. |
| Workspace Operador (Kanban/Lista + SLA visual) | Planejado | 4 | Sortable.js + HTMX. |
| Notas internas (`is_interna`) | Planejado | 4 | Invisível ao CLIENTE (RLS + Realtime). |
| Chat Realtime (supabase-js direto) | Planejado | 3–4 | Fallback polling 5s. |
| Painel Admin + KPIs + Export CSV | Planejado | 5 | Chart.js 4. |
| SLA — validação com gestor | Planejado | pré-4 | C1 / Seção 5.2. |
| Cache tenant-scoped (categorias/planos) | Planejado | 2–3 | Local por-processo; Redis se >1 réplica. |
| Rate limiting (slowapi) | Planejado | pré-deploy | In-memory; Redis se >1 réplica. |
| Observabilidade (logs estruturados + request-id) | Planejado | 1+ | Critério go-live: 48h sem 5xx. |

---

### Pendências de validação consolidadas (`⚠️`)

- **SLA:** escada de fallback, unidade (corridas vs comercial/feriados), eventos de pausa (`AGUARDANDO`) — **validar com o gestor antes da Fase 4**.
- **Prioridades:** confirmar conjunto `BAIXA/MEDIA/ALTA/URGENTE`.
- **Perfis:** `empresa_id` de OPERADOR/ADMIN (NULL vs empresa matriz).
- **Categorias:** globais vs por-tenant (adotado global).
- **JWT:** JWKS assimétrico vs HS256 legado (confirmar no projeto Supabase).
- **Realtime + RLS:** confirmar que a entrega respeita `is_interna` e CSP `connect-src wss`.
- **Versões `⚠️ A CONFIRMAR`:** travar patches exatos no lockfile no setup (Seção 0).
- **Relatórios para OPERADOR:** confirmar se só ADMIN acessa.
- **CSAT:** depende de e-mail transacional (pode ir a backlog).

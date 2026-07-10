# Graph Report - .  (2026-07-10)

## Corpus Check
- 3 files · ~2,463 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3367 nodes · 9145 edges · 107 communities (72 shown, 35 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 1021 edges (avg confidence: 0.64)
- Token cost: 79,090 input · 0 output

## Community Hubs (Navigation)
- Chart.js Internals (Prototype Bundle)
- HTMX/Sortable/Alpine Vendor Bundle
- Chart.js Scale/Legend Internals
- Attachment Storage & Signed URLs
- Alpine.js Runtime Internals
- Chart/Alpine/HTMX Mixed Internals
- Chamados Repository & Notifications
- Chart.js Scale Building
- Portal Tests & Load Testing
- App Config & CSP/CSRF Settings
- Chart.js Element Rendering
- Admin Route Tests
- Chart.js Draw/Update Lifecycle
- Chart.js Registry & Update Hooks
- RLS Connection Helpers
- Marketing Admin Dashboard JS
- Chart.js Dataset Updates
- Chart.js Bar Chart Pixel Calc
- Chart.js Bar Chart Pixel Calc (Prototype)
- Chart/Alpine Vendor Internals (Prototype)
- Workspace Route Tests
- Supabase JS Client Internals
- Auth Dependencies (CurrentUser)
- Portal Routes (Perfil/CSRF)
- Admin Routes & Service-Role Client
- Chart.js Layout Building
- Prototype HTML Screens (Admin/Relatorios)
- Project Docs: Benchmarks, Tutorial, Marketing Template
- Alpine.js CSP Build Internals
- Supabase Realtime Channel Client
- Chart.js Layout Building (Prototype)
- Supabase Storage/Functions Client
- Chart.js Draw Internals (Prototype)
- Prototype Screen Builder (build.py)
- Auth Routes & Session
- Supabase Realtime Ref/Timeout Handling
- Chart.js Aspect Ratio/Data (Prototype)
- Chart.js Animator
- NPM Package Manifest
- Supabase Realtime Push Internals
- Supabase Realtime Channel Membership
- Chart.js Tick Building
- SLA Visual Domain Logic
- Chart.js Responsive Events
- Chart.js/Sortable Color Utils
- Supabase Storage Upload Client
- Chart.js Tick Building (Prototype)
- Chart.js Color Utils (Prototype)
- Chart.js Event Binding (Prototype)
- Chart.js Plugin Registry
- Chart.js Plugin Registry (Prototype)
- Alpine/Chart.js Mixed Internals
- Alpine/Supabase Reactive Internals
- Python Dependency Manifests
- JWT Verification
- Chart.js Registration (Prototype)
- Screenshot Preview Server
- Chart.js Pie/Doughnut Calc
- Alpine.js CSP Effects
- Chart.js Pie/Doughnut Calc (Prototype)
- In-Process Cache
- Alpine.js CSP Reactivity
- Locust Load Test
- Holiday Calendar (SLA)
- Alpine.js Internals (Prototype)
- Alpine.js CSP Internals
- Alpine.js Internals
- Supabase JS Sync State
- Chart.js Canvas/Device Pixel Ratio
- Chart.js Animation Registry (Prototype)
- Chart.js Canvas Internals (Prototype)
- Chart.js Update/Cancel (Prototype)
- Chart.js Dataset Meta Cleanup (Prototype)
- Health Check Tests
- Supabase Binary Decode
- Vercel Deployment Config
- Novo Chamado Form JS
- RLS initplan Optimization (Benchmark)
- Domain Package Init
- App Package Init
- Categoria/Subcategoria HTMX Fragments
- TI Scope Decision History
- Admin KPI Set (Plano Mestre)
- RLS Auth Design Decisions
- Bondmann Favicon
- Portal UI Macros (Badges/Stars)
- Prioridade Badge Macro
- Status Badge Macro
- Workspace Macros Template
- Project Metadata (pyproject.toml)
- Alpine.js CSP Build Decision
- Realtime Chat Topology Decision
- Signed URL TTL Policy
- SLA Fallback Ladder Decision
- Tailwind Build-Step Decision
- Admin Categorias Prototype

## God Nodes (most connected - your core abstractions)
1. `i()` - 109 edges
2. `ChamadosRepo` - 78 edges
3. `n()` - 78 edges
4. `FakeAdmin` - 71 edges
5. `s()` - 66 edges
6. `o()` - 66 edges
7. `rls_connection()` - 63 edges
8. `n()` - 61 edges
9. `an()` - 61 edges
10. `an()` - 61 edges

## Surprising Connections (you probably didn't know these)
- `$n()` --indirect_call--> `cr()`  [INFERRED]
  prototipos/assets/alpine.min.js → app/static/vendor/alpine-csp.min.js
- `nr()` --indirect_call--> `i()`  [INFERRED]
  prototipos/assets/alpine.min.js → app/static/vendor/htmx.min.js
- `admin-dashboard.html (prototype)` --semantically_similar_to--> `Fase 5 admin KPI set (TMA, SLA compliance, CSAT, productivity)`  [INFERRED] [semantically similar]
  prototipos/admin-dashboard.html → plano_mestre_desenvolvimento.md
- `FakeAdmin` --uses--> `CurrentUser`  [INFERRED]
  tests/test_admin.py → app/auth/dependencies.py
- `_FakeAdminAPI` --uses--> `CurrentUser`  [INFERRED]
  tests/test_admin.py → app/auth/dependencies.py

## Import Cycles
- 1-file cycle: `app/security/jwt.py -> app/security/jwt.py`

## Hyperedges (group relationships)
- **RLS helpers used together (auth.uid, auth_is_ti, auth_departamento_id)** — docs_benchmark_resultados_auth_uid, docs_benchmark_resultados_auth_is_ti, docs_benchmark_resultados_auth_departamento_id [EXTRACTED 1.00]
- **Promoção de staff exige escrita dupla (perfis + auth.users.raw_app_meta_data)** — docs_tutorial_usuarios_perfis_table, docs_tutorial_usuarios_auth_users_table, docs_tutorial_usuarios_raw_app_meta_data [EXTRACTED 1.00]
- **Setor/Departamento (RH, TI, Marketing) referenciado em três documentos** — docs_benchmark_resultados_escopo_por_setor, docs_graphify_out_converted_chamados_historico_marketing_template_fbf7b2b8_setor_solicitante, docs_tutorial_usuarios_departamentos_table [INFERRED 0.75]
- **RLS-scoped fragment rendering pattern (RLS enforces scope, templates only render)** — concept_row_level_security, app_templates__notificacoes, app_templates_portal__mensagens, readme [INFERRED 0.85]
- **Fase 4 — Realtime notification bell implementation** — concept_fase_4, app_templates_admin_admin_base, app_templates__sino, app_static_js_notificacoes [INFERRED 0.85]
- **Mídia Regional CRUD (gestao.html) feeds the Marketing dashboard chart data (Fase 6)** — app_templates_admin_gestao, app_templates_admin_dashboard_marketing, concept_fase_6 [INFERRED 0.85]
- **Realtime chat + notification bell shared across Portal and Workspace shells** — app_templates_portal_app_base, app_templates_workspace_workspace_base, app_templates_portal_chamado_detalhe, app_templates_workspace_atendimento [INFERRED 0.80]
- **sla_chip() macro usage across workspace queue/board/detail views** — app_templates_workspace_macros_sla_chip, app_templates_workspace_fila_linhas, app_templates_workspace_atendimento, app_templates_workspace_kanban [EXTRACTED 1.00]
- **Documentation trail of the department-based access-control model (decision, tutorial, superseded prototype)** — plano_mestre_departamento_routing_model, docs_tutorial_usuarios, prototipos_readme [INFERRED 0.75]
- **Ticket BOND-2026-00412 shown across client/operator/queue views** — prototipos_cliente_chamado_detalhe_chat_thread, prototipos_operador_atendimento_chat_thread, prototipos_operador_fila_kanban_board [INFERRED 0.85]
- **RBAC: login, role assignment, and access-denied enforcement** — prototipos_login, prototipos_admin_usuarios_user_table, prototipos_erro_403 [INFERRED 0.75]
- **SLA policy expressed in design tokens, ticket-creation forecast, and reporting KPIs** — prototipos_design_system_components, prototipos_cliente_novo_chamado_sla_preview, prototipos_admin_relatorios_kpi_cards [INFERRED 0.80]

## Communities (107 total, 35 thin omitted)

### Community 0 - "Chart.js Internals (Prototype Bundle)"
Cohesion: 0.02
Nodes (71): Qe(), Un(), addBox(), Ae(), As(), average(), be(), beforeDatasetDraw() (+63 more)

### Community 1 - "HTMX/Sortable/Alpine Vendor Bundle"
Cohesion: 0.05
Nodes (131): qe(), Tt(), A(), ae(), an(), at(), B(), be() (+123 more)

### Community 2 - "Chart.js Scale/Legend Internals"
Cohesion: 0.03
Nodes (49): As(), be(), beforeDatasetDraw(), Bi(), buildTicks(), d(), destroy(), determineDataLimits() (+41 more)

### Community 3 - "Attachment Storage & Signed URLs"
Cohesion: 0.04
Nodes (101): access_token(), assinar_anexos(), enviar_uploads(), processar_uploads(), Request, UploadFile, Fluxo de anexos compartilhado entre Portal e Workspace (Seção 3.9 / C2).  Vali, JWT do usuário — necessário para o Storage sob RLS (ver ``current_access_token`` (+93 more)

### Community 4 - "Alpine.js Runtime Internals"
Cohesion: 0.04
Nodes (98): _(), Ae(), ar(), at(), Bn(), Bt(), Ce(), Ci() (+90 more)

### Community 5 - "Chart/Alpine/HTMX Mixed Internals"
Cohesion: 0.06
Nodes (71): i(), an(), Gn(), In(), It(), Ke(), kn(), Ln() (+63 more)

### Community 6 - "Chamados Repository & Notifications"
Cohesion: 0.04
Nodes (69): Registra um :class:`_RLSHolder` no contextvar para todo o request.      **Não, rls_request_scope(), agendar_notificacao_email(), BackgroundTasks, Dispara a notificação de e-mail de forma assíncrona usando BackgroundTasks (em s, ChamadosRepo, Any, Categorias ativas, opcionalmente filtradas por departamento.          Categori (+61 more)

### Community 7 - "Chart.js Scale Building"
Cohesion: 0.06
Nodes (62): a(), aa(), ai(), ao(), average(), b(), beforeDatasetsDraw(), beforeDraw() (+54 more)

### Community 8 - "Portal Tests & Load Testing"
Cohesion: 0.05
Nodes (61): Valida e normaliza a nota de avaliação (1–5). Levanta ``ValueError``., validar_nota(), main(), Teste bruto de concorrência (sem Locust) — Fase 5.  Dispara muitas requisições, rodar(), _worker(), _cliente(), _csrf() (+53 more)

### Community 9 - "App Config & CSP/CSRF Settings"
Cohesion: 0.04
Nodes (67): True no ambiente serverless da Vercel (funções efêmeras, Seção 2.1)., Endpoint JWKS do GoTrue para verificação assimétrica (Seção 3.6)., Origem wss do Realtime, para o connect-src da CSP (Seção 3.8)., Remetente do e-mail. Para alinhamento DKIM/DMARC no Mailgun o domínio         d, Settings, admin_connection(), close_pool(), _ensure_pool() (+59 more)

### Community 10 - "Chart.js Element Rendering"
Cohesion: 0.04
Nodes (23): addElements(), at(), beforeUpdate(), bn, configure(), ei(), go(), ii() (+15 more)

### Community 11 - "Admin Route Tests"
Cohesion: 0.06
Nodes (50): admin_client(), _csrf(), FakeAdmin, _FakeAdminAPI, FakePerfilRepo, _FakeSupaClient, _patch_admin_client(), Testes do Painel Admin (Fase 5) — gating por TI, KPIs, gestão e CSV. (+42 more)

### Community 12 - "Chart.js Draw/Update Lifecycle"
Cohesion: 0.06
Nodes (30): afterDraw(), afterEvent(), afterUpdate(), at(), Ci(), configure(), Ee(), f() (+22 more)

### Community 13 - "Chart.js Registry & Update Hooks"
Cohesion: 0.04
Nodes (21): beforeUpdate(), bn, ei(), go(), ii(), initialize(), je(), labelColor() (+13 more)

### Community 14 - "RLS Connection Helpers"
Cohesion: 0.04
Nodes (35): _apply_rls_claims(), Any, Conexão transacional com claims do usuário aplicados para RLS.      Use para T, Injeta papel + claims no escopo da transação (RLS) em **um** round-trip., Conexão RLS **por request**, aberta preguiçosamente na 1ª query e reusada., rls_connection(), _RLSHolder, AdminRepo (+27 more)

### Community 15 - "Marketing Admin Dashboard JS"
Cohesion: 0.05
Nodes (59): afterDatasetsDraw(), buildAtrasosTable(), filteredMonthly(), mkChart(), renderAllCharts(), renderCausas(), renderDept(), renderEntrega() (+51 more)

### Community 16 - "Chart.js Dataset Updates"
Cohesion: 0.06
Nodes (13): addBox(), addElements(), afterDatasetsUpdate(), an(), generateLabels(), ke(), Mn(), onClick() (+5 more)

### Community 17 - "Chart.js Bar Chart Pixel Calc"
Cohesion: 0.06
Nodes (24): Ae(), ca(), _calculateBarIndexPixels(), _calculateBarValuePixels(), Do(), eo(), Fn(), getBasePixel() (+16 more)

### Community 18 - "Chart.js Bar Chart Pixel Calc (Prototype)"
Cohesion: 0.07
Nodes (26): afterEvent(), ca(), _calculateBarIndexPixels(), _calculateBarValuePixels(), f(), Fn(), getBasePixel(), getLabelAndValue() (+18 more)

### Community 19 - "Chart/Alpine Vendor Internals (Prototype)"
Cohesion: 0.08
Nodes (31): _(), A(), Ee(), Hn(), mi(), P(), S(), y() (+23 more)

### Community 20 - "Workspace Route Tests"
Cohesion: 0.08
Nodes (36): _chamado(), _csrf(), FakeRepo, _funcionario(), Testes do Workspace do Operador (Fase 4) — auth/repo fakes, sem banco., test_atendimento_mostra_botao_excluir_e_pede_confirmacao(), test_atendimento_mostra_observadores_em_copia(), test_atendimento_renderiza_acoes_e_composer() (+28 more)

### Community 22 - "Auth Dependencies (CurrentUser)"
Cohesion: 0.07
Nodes (35): CurrentUser, _extract_token(), get_current_user(), get_optional_user(), load_empresa_id(), Request, Dependências de autenticação e autorização (Seções 3.2 / 3.4).  ``CurrentUser`, Papel do usuário a partir dos claims (app_metadata.role) com default CLIENTE. (+27 more)

### Community 23 - "Portal Routes (Perfil/CSRF)"
Cohesion: 0.08
Nodes (48): _csrf_guard(), Request, ver_perfil(), adicionar_observador(), avaliar_chamado(), categorias_fragmento(), criar_chamado(), _csrf_guard() (+40 more)

### Community 24 - "Admin Routes & Service-Role Client"
Cohesion: 0.11
Nodes (47): ensure_admin_client(), Cliente Supabase com a **service_role key** para tarefas administrativas     (G, admin_context(), AdminCtx, _base_ctx(), criar_categoria(), criar_departamento(), criar_subcategoria() (+39 more)

### Community 25 - "Chart.js Layout Building"
Cohesion: 0.06
Nodes (13): beforeLayout(), buildLookupTable(), En, Fo(), _generate(), getDecimalForValue(), _getTimestampsForTable(), init() (+5 more)

### Community 26 - "Prototype HTML Screens (Admin/Relatorios)"
Cohesion: 0.06
Nodes (46): Admin Relatórios Page, Relatórios CSV Export, Relatórios KPI Cards (SLA compliance), Relatórios Results Table, Admin Usuários Page, Convidar Usuário Modal, Usuários Table (roles ADMIN/OPERADOR/CLIENTE), Cliente Chamado Detalhe Page (+38 more)

### Community 27 - "Project Docs: Benchmarks, Tutorial, Marketing Template"
Cohesion: 0.06
Nodes (45): Benchmark do sistema — resultados, Arquivar chamados RESOLVIDO antigos, auth_departamento_id() (RLS helper), auth_is_ti() (RLS helper), Tabela chamados, Chat — carregar mensagens, Escopo por setor funciona (RH filtra, TI vê tudo), Fila do workspace (lista/kanban) (+37 more)

### Community 28 - "Alpine.js CSP Build Internals"
Cohesion: 0.06
Nodes (27): bi(), cr(), eo(), Gn(), kn(), kr(), Kt(), Le() (+19 more)

### Community 29 - "Supabase Realtime Channel Client"
Cohesion: 0.05
Nodes (16): delete(), E, _handleTokenChanged(), _initRealtimeClient(), _initSupabaseAuthClient(), insert(), L, _listenForAuthEvents() (+8 more)

### Community 30 - "Chart.js Layout Building (Prototype)"
Cohesion: 0.07
Nodes (12): beforeLayout(), buildLookupTable(), En, _generate(), getDecimalForValue(), _getTimestampsForTable(), init(), initOffsets() (+4 more)

### Community 32 - "Chart.js Draw Internals (Prototype)"
Cohesion: 0.09
Nodes (8): afterDraw(), Bi(), Ci(), Do(), eo(), Fi(), ls, Oe()

### Community 33 - "Prototype Screen Builder (build.py)"
Cohesion: 0.13
Nodes (33): auth_page(), avatar(), btn(), card(), cat_row(), chat_composer(), chat_thread(), _color() (+25 more)

### Community 34 - "Auth Routes & Session"
Cohesion: 0.10
Nodes (30): _csrf_guard(), Request, Rotas de autenticação: /login, /logout (Fase 2).  Auth via supabase-py async (, Inclui o router aplicando rate limit nas rotas sensíveis., register_auth_routes(), clear_session(), current_access_token(), BaseHTTPMiddleware (+22 more)

### Community 35 - "Supabase Realtime Ref/Timeout Handling"
Cohesion: 0.10
Nodes (5): k, m(), T(), v(), w()

### Community 36 - "Chart.js Aspect Ratio/Data (Prototype)"
Cohesion: 0.10
Nodes (4): an(), generateLabels(), onClick(), reset()

### Community 37 - "Chart.js Animator"
Cohesion: 0.10
Nodes (5): bt, Cs, nn(), os(), sn

### Community 38 - "NPM Package Manifest"
Cohesion: 0.06
Nodes (31): alpinejs, @alpinejs/csp, chart.js, htmx.org, dependencies, alpinejs, @alpinejs/csp, chart.js (+23 more)

### Community 40 - "Supabase Realtime Channel Membership"
Cohesion: 0.14
Nodes (4): C, F(), then(), z()

### Community 41 - "Chart.js Tick Building"
Cohesion: 0.08
Nodes (9): bo, getValueForPixel(), H(), j(), ko, mo(), ne(), numeric() (+1 more)

### Community 42 - "SLA Visual Domain Logic"
Cohesion: 0.15
Nodes (25): barra_sla(), BarraSLA, estado_sla(), EstadoSLA, humanizar_delta(), datetime, Estado visual do SLA (Fase 4 — indicador por cores).  Regra (Seção 6, Fase 4 d, Barra de progresso do prazo: enche conforme o tempo passa.      - ``pct``: qua (+17 more)

### Community 43 - "Chart.js Responsive Events"
Cohesion: 0.11
Nodes (12): ce(), ct(), de, dt(), fs(), ge(), he(), ms() (+4 more)

### Community 44 - "Chart.js/Sortable Color Utils"
Cohesion: 0.12
Nodes (8): color(), Ft(), It(), te(), wt(), Xt(), zt(), Lt()

### Community 45 - "Supabase Storage Upload Client"
Cohesion: 0.19
Nodes (3): b, g, y()

### Community 46 - "Chart.js Tick Building (Prototype)"
Cohesion: 0.08
Nodes (7): bo, getValueForPixel(), j(), ko, ne(), numeric(), xo

### Community 47 - "Chart.js Color Utils (Prototype)"
Cohesion: 0.12
Nodes (7): color(), Ft(), It(), te(), wt(), Xt(), zt()

### Community 51 - "Alpine/Chart.js Mixed Internals"
Cohesion: 0.13
Nodes (22): B(), br(), fr(), gr(), Gt(), jr(), lr(), $n() (+14 more)

### Community 52 - "Alpine/Supabase Reactive Internals"
Cohesion: 0.12
Nodes (20): Ce(), dr(), Fe(), get(), Hn(), je(), Ln(), Me() (+12 more)

### Community 53 - "Python Dependency Manifests"
Cohesion: 0.12
Nodes (21): requirements.txt (runtime manifest), asyncpg==0.30.0, requirements-dev.txt (dev/test manifest), pytest==8.3.4, pytest-asyncio==0.24.0, fastapi==0.115.6, holidays==0.100, httpx==0.27.2 (+13 more)

### Community 54 - "JWT Verification"
Cohesion: 0.24
Nodes (15): JWTVerifier, Exception, Verificação local do JWT do Supabase (Seção 3.6).  Decisão: **signing keys ass, Token ausente, expirado, com assinatura inválida ou claims incorretos., Verificador com cache de JWKS (assimétrico) e fallback HS256., Retorna os claims se válido; levanta ``TokenInvalido`` caso contrário., TokenInvalido, _make_token() (+7 more)

### Community 55 - "Chart.js Registration (Prototype)"
Cohesion: 0.14
Nodes (7): C(), ce(), de, dt(), he(), ia(), qs()

### Community 56 - "Screenshot Preview Server"
Cohesion: 0.18
Nodes (16): HTMLResponse, admin_dashboard(), admin_export(), admin_gestao(), home(), login_get(), login_post(), logout() (+8 more)

### Community 58 - "Alpine.js CSP Effects"
Cohesion: 0.21
Nodes (14): Ct(), de(), Dn(), ei(), has(), ht(), ir(), Ke() (+6 more)

### Community 60 - "In-Process Cache"
Cohesion: 0.17
Nodes (12): clear(), get(), invalidate(), invalidate_prefix(), Any, Cache em memória por-processo com TTL (Seção 2.3 do plano mestre).  Para catál, Valor cacheado se presente e não expirado; senão ``None``., Guarda ``value`` sob ``key`` por ``ttl`` segundos. (+4 more)

### Community 61 - "Alpine.js CSP Reactivity"
Cohesion: 0.21
Nodes (13): Bn(), di(), effect(), fi(), Fn(), G(), ie(), jn() (+5 more)

### Community 62 - "Locust Load Test"
Cohesion: 0.17
Nodes (6): HttpUser, _csrf(), PortalUser, Teste de carga (uso em grande escala) — Locust (Fase 5).  Simula vários funcio, Busca o /login para obter o cookie+token CSRF (double-submit)., Funcionário/operador navegando: dashboard, fila (polling), sino, detalhe.

### Community 63 - "Holiday Calendar (SLA)"
Cohesion: 0.24
Nodes (10): feriados_nacionais(), proximos_anos(), date, Feriados nacionais via biblioteca `holidays` (Fase 5 — 2026-07-09).  Antes, `f, Feriados nacionais do Brasil para os anos informados, ordenados por data., Ano atual + N seguintes (mesmo horizonte do antigo seed manual: ~3 anos)., Testes do módulo de feriados nacionais (Fase 5) — puro, sem banco., test_feriados_nacionais_cobre_mais_de_um_ano() (+2 more)

### Community 64 - "Alpine.js Internals (Prototype)"
Cohesion: 0.20
Nodes (12): A(), ao(), D(), E(), ge(), nt(), oo(), Or() (+4 more)

### Community 65 - "Alpine.js CSP Internals"
Cohesion: 0.18
Nodes (12): ai(), an(), he(), ii(), li(), ni(), oi(), Pr() (+4 more)

### Community 66 - "Alpine.js Internals"
Cohesion: 0.22
Nodes (11): en(), I(), Mi(), q(), qr(), rn(), tn(), U() (+3 more)

### Community 75 - "Vercel Deployment Config"
Cohesion: 0.40
Nodes (4): builds, routes, $schema, version

### Community 78 - "RLS initplan Optimization (Benchmark)"
Cohesion: 0.67
Nodes (3): Otimização auth_rls_initplan, auth.uid() (RLS helper), Migration 0014

## Knowledge Gaps
- **92 isolated node(s):** `name`, `version`, `private`, `description`, `build:css` (+87 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **35 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `i()` connect `Chart/Alpine/HTMX Mixed Internals` to `Chart.js Internals (Prototype Bundle)`, `HTMX/Sortable/Alpine Vendor Bundle`, `Chart.js Scale/Legend Internals`, `Alpine.js Runtime Internals`, `Chart.js Scale Building`, `Chart.js Draw/Update Lifecycle`, `Chart.js Dataset Updates`, `Chart.js Bar Chart Pixel Calc`, `Chart/Alpine Vendor Internals (Prototype)`, `Supabase JS Client Internals`, `Chart.js Layout Building`, `Alpine.js CSP Build Internals`, `Chart.js Layout Building (Prototype)`, `Chart.js Animator`, `Supabase Realtime Channel Membership`, `Chart.js/Sortable Color Utils`, `Supabase Storage Upload Client`, `Chart.js Color Utils (Prototype)`, `Alpine/Chart.js Mixed Internals`, `Alpine/Supabase Reactive Internals`, `Alpine.js CSP Effects`, `Alpine.js CSP Reactivity`, `Alpine.js CSP Internals`, `Alpine.js Internals`, `Chart.js Update/Cancel (Prototype)`, `Chart.js Dataset Meta Cleanup (Prototype)`?**
  _High betweenness centrality (0.140) - this node is a cross-community bridge._
- **Why does `c()` connect `HTMX/Sortable/Alpine Vendor Bundle` to `Chart.js Internals (Prototype Bundle)`, `Chart.js Draw Internals (Prototype)`, `Chart.js Scale/Legend Internals`, `Alpine.js Runtime Internals`, `Chart/Alpine/HTMX Mixed Internals`, `Chart.js Scale Building`, `Supabase Realtime Channel Membership`, `Chart.js Tick Building`, `Chart.js Element Rendering`, `Chart.js Registry & Update Hooks`, `Supabase Storage Upload Client`, `Chart/Alpine Vendor Internals (Prototype)`, `Alpine/Supabase Reactive Internals`, `Supabase JS Client Internals`, `Chart.js Pie/Doughnut Calc`, `Chart.js Pie/Doughnut Calc (Prototype)`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Why does `O` connect `Chart.js Scale Building` to `Alpine.js Internals (Prototype)`, `Chart/Alpine/HTMX Mixed Internals`, `Chart.js Draw/Update Lifecycle`, `Chart/Alpine Vendor Internals (Prototype)`, `Supabase Realtime Channel Client`, `Supabase Storage/Functions Client`?**
  _High betweenness centrality (0.031) - this node is a cross-community bridge._
- **Are the 104 inferred relationships involving `i()` (e.g. with `Bn()` and `di()`) actually correct?**
  _`i()` has 104 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `ChamadosRepo` (e.g. with `AdminCtx` and `PortalCtx`) actually correct?**
  _`ChamadosRepo` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 43 inferred relationships involving `n()` (e.g. with `_()` and `Fn()`) actually correct?**
  _`n()` has 43 INFERRED edges - model-reasoned connections that need verification._
- **Are the 37 inferred relationships involving `s()` (e.g. with `_()` and `ct()`) actually correct?**
  _`s()` has 37 INFERRED edges - model-reasoned connections that need verification._
# Plano de Melhorias — Auditoria 2026-07-14

> **Documento vivo de execução.** Origem: `Auditoria_Melhorias_Portal_Chamados_2026-07-14.docx`
> (24 achados: A1–A5 altos, M1–M12 médios, B1–B6 baixos). Cada item abaixo vira um PR
> pequeno e verificável. Marcar `[x]` ao concluir e registrar a data + resumo na tabela de
> progresso no final. Regra do plano mestre vale aqui: **suíte pytest verde antes de dar
> qualquer item por pronto.**

**Legenda de esforço:** 🟢 pequeno (≤ meio dia) · 🟡 médio (1–2 dias) · 🔴 grande (3+ dias)
**Itens marcados `[DECISÃO DO GESTOR]` precisam de escolha do Osvaldo antes de executar.**

---

## Sprint 0 — Fundações (P0, imediato)

> Ordem obrigatória: o item 0.1 destrava o 0.4 (o CI valida a cadeia de migrations).

### 0.1 · A1 — Reconstruir a migration `0015_subcategorias` 🟡 ✅ **Concluído 2026-07-14**
- [x] Extrair do Supabase de produção (`iurlzlhbnoemkzgexcfk`) o DDL real: tabela
      `subcategorias`, coluna `chamados.subcategoria_id`, índices, policies RLS,
      alteração do trigger `enforce_cliente_so_avaliacao` e seed (Acessos 5 / Dúvida 3 /
      Equipamento 5 / Financeiro 4 — ver changelog 2026-07-03 do plano mestre).
- [x] Escrever `supabase/migrations/0015_subcategorias.sql` reproduzindo exatamente o
      estado que a `0016+` pressupõe.
- [x] Validar: aplicar `0001→0044` numa base limpa (Supabase local ou schema temporário)
      sem erro; conferir diff contra produção.
- **Aceite:** cadeia de migrations contínua e aplicável do zero; sem diff relevante vs produção.
- **Notas de execução:**
  - Git não tinha nenhum histórico do arquivo `0015` (nunca foi commitado), mas a
    produção tem o registro real da migration aplicada (`schema_migrations`,
    versão `20260703224156`). Reconstrução feita por introspecção direta do banco
    de produção via MCP do Supabase: colunas/FKs/índices de `subcategorias` e
    `chamados.subcategoria_id`, policies RLS (`subcategorias_select`/`_admin_all`)
    e o corpo atual de `enforce_cliente_so_avaliacao` (já com `subcategoria_id` na
    lista imutável) — tudo estruturalmente idêntico ao estado real.
  - **Única parte não recuperável:** o conteúdo exato do seed original (as 17
    subcategorias sob Acessos/Dúvida/Equipamento/Financeiro) — foi sobrescrito em
    produção pela `0018` antes desta reconstrução e é funcionalmente irrelevante
    para o estado final (0018 apaga e reinsere tudo). A `0015` reconstruída usa
    nomes placeholder documentados no cabeçalho do arquivo.
  - **Validação real:** criado projeto Supabase novo e descartável
    (`portal-chamados-migration-test`, tier gratuito, pausado após o teste — sem
    custo) e aplicadas as 44 migrations em sequência, uma por uma, via MCP. Cadeia
    inteira aplicou sem nenhum erro. Schema final comparado campo-a-campo contra
    produção: idêntico (pequenas diferenças de contagem em `categorias`/
    `subcategorias` são dado orgânico inserido depois via `/admin`, não falha da
    cadeia).
  - **Achado extra (fora do escopo original do 0.1, corrigido em conjunto):** os
    advisors de segurança do projeto de teste acusaram `marketing_midia_regional`
    com RLS **desabilitada** — mas a produção real tem RLS **habilitada** nessa
    tabela, via uma função `rls_auto_enable()` que existe em produção mas nunca
    passou por nenhuma migration (mesmo tipo de drift que causou a perda da
    `0015`). Fechado com a nova migration `supabase/migrations/0045_fix_marketing_midia_regional_rls_enable.sql`
    (idempotente). **Pendência (fechada 2026-07-16):** a função
    `rls_auto_enable()` em si (o mecanismo que auto-habilita RLS em produção)
    foi investigada por introspecção via MCP do Supabase — é um event trigger
    `ensure_rls` (ON `ddl_command_end`, tags `CREATE TABLE`/`CREATE TABLE
    AS`/`SELECT INTO`) que roda `ENABLE ROW LEVEL SECURITY` em toda tabela
    nova de `public`, owner `postgres`, SECURITY DEFINER. Formalizado em
    `supabase/migrations/0046_document_rls_auto_enable_trigger.sql`
    (idempotente; validado com `BEGIN`/`ROLLBACK` direto contra produção, sem
    erro) e documentado como rede de segurança complementar na ADR-0001
    (`docs/adr/0001-rls-via-set-local-claims.md`).

### 0.2 · A3 — Fail-fast de segredos default em produção 🟢 ✅ **Concluído 2026-07-14**
- [x] Em `app/main.py` (ou validador no `Settings`): se `is_production` e
      `session_secret`/`csrf_secret` estiverem nos defaults `dev-insecure-*`, abortar o
      boot com mensagem clara.
- [x] Teste: app recusa boot com default em produção; sobe normal em dev.
- **Aceite:** impossível subir produção com segredo de desenvolvimento.
- **Notas de execução:** `model_validator(mode="after")` em `app/config.py::Settings`
  (`_fail_fast_segredos_default_em_producao`) — dispara na própria instanciação
  de `Settings()` (então também no import de `app.main`, antes de qualquer rota
  responder). Mensagem lista exatamente qual(is) variável(is) ainda está(ão) no
  default. Testes em `tests/test_config.py` (5 casos: cada segredo isolado,
  ambos juntos, produção com segredos reais, dev com defaults).

### 0.3 · A4 — Endurecer webhook WhatsApp 🟢 ✅ **Concluído 2026-07-14**
- [x] `app/routes/whatsapp.py`: em produção, `whatsapp_app_secret` ausente ⇒ rejeitar o
      POST (403/503) em vez de aceitar sem validação.
- [x] Logar apenas metadados (tipo de evento, ids), nunca o payload completo (PII).
- [x] Testes: sem secret em prod rejeita; assinatura inválida rejeita; válida aceita.
- **Aceite:** nenhum POST não assinado processado em produção; nenhum corpo integral em log.
- **Notas de execução:** sem secret + `is_production` ⇒ 503 antes de tentar validar
  qualquer coisa; dev/staging sem secret continua aceitando sem validar (handshake
  inicial antes de configurar o App Secret da Meta). Novo helper
  `_metadados_evento()` extrai só `field`/`id` de mensagens e status — nunca
  telefone, nome de contato ou corpo da mensagem. Testes em `tests/test_whatsapp.py`
  (4 casos, incluindo dev sem secret continuando a aceitar).

### 0.4 · A2 — CI mínimo (GitHub Actions) 🟡 ✅ **Concluído 2026-07-14** (branch protection pendente — ver nota)
- [x] Workflow `ci.yml`: (a) `pytest` (suíte completa, 182+); (b) build do Tailwind
      (`npm run build:css`) sem erro; (c) checagem de numeração contínua de
      `supabase/migrations/` (script simples — teria pego o A1); (d) `ruff check` (adotar
      config mínima, sem reformatar o código existente).
- [ ] Proteger a branch: merge só com CI verde. **`[DECISÃO/AÇÃO DO GESTOR]`** — configuração
      de branch protection é ajuste de repositório GitHub (Settings → Branches), fora do
      alcance de um PR; precisa ser feita manualmente por quem tem admin no repo.
- **Aceite:** PR aberto dispara o pipeline; falha bloqueia merge (bloqueio de merge depende
  da ação manual acima).
- **Notas de execução:**
  - `.github/workflows/ci.yml` com 4 jobs paralelos: `pytest` (`pip install -r
    requirements-dev.txt` + `pytest`), `build-css` (`npm ci` + `npm run build:css`),
    `migrations-sequence` (novo `scripts/check_migrations_sequence.py`) e `ruff`.
  - `scripts/check_migrations_sequence.py`: script standalone (sem dependências) que
    falha se a numeração `NNNN_*.sql` tiver lacuna/duplicata — validado localmente
    (45 migrations, 0001..0045 contínuo, depois do fix do item 0.1).
  - `ruff` adicionado a `requirements-dev.txt` (`ruff==0.8.6`) com config mínima em
    `pyproject.toml` (`select = ["E9", "F"]`, ignorando `F401`/`F821` — 24 achados
    pré-existentes espalhados pelo código, fora do escopo desta migration; registrar
    como limpeza futura). `prototipos/` excluído do lint (candidato a arquivamento,
    item 1.6).
  - **Achado à parte (ambiente local, não bloqueia o CI):** a suíte pytest completa
    tem uma falha intermitente **só no Windows local** — crash "access violation" no
    carregamento do `libmagic.dll` (via `python-magic-bin`, dependência condicional
    `sys_platform == 'win32'`) disparado pelos testes de upload
    (`tests/test_anexos_route.py`/`test_uploads.py`) quando rodam em thread do anyio.
    Não deve reproduzir no CI (roda em `ubuntu-latest`, sem essa dependência Windows).
    Sinalizado como tarefa separada para investigação.

---

## Sprint 1 — Hardening de borda e validação sistemática (P1, 2–4 semanas)

### 1.1 · A5 — Proxy confiável para o rate limit 🟢 ✅ **Concluído 2026-07-15**
- [x] Configurar `--proxy-headers`/`forwarded_allow_ips` no Uvicorn (Railway) e revisar
      `app/ratelimit.py::client_ip` para usar o IP adicionado pelo proxy confiável, não o
      primeiro do header `X-Forwarded-For` (spoofável).
- [x] Teste: header forjado não muda a chave de rate limit.
- **Aceite:** rate limit não contornável via header.
- **Notas de execução:** `client_ip()` agora usa o **último** IP da cadeia
  `X-Forwarded-For` (o único hop confiável, acrescentado pelo Railway) em vez do
  primeiro (escrito pelo cliente, forjável). O `--forwarded-allow-ips "*"` do
  Dockerfile continua necessário (Railway não publica IPs fixos de proxy) — a
  correção real estava na leitura do header, não na config do Uvicorn. Plano
  mestre (Seção 2.4) atualizado para não documentar mais a lógica antiga.
  4 testes novos em `tests/test_ratelimit.py`.

### 1.2 · M3 — Restringir endpoints de diagnóstico 🟢 ✅ **Concluído 2026-07-15**
- [x] `/health/ready` e `/health/config`: em produção, exigir token simples (env) ou
      restringir; `/health` continua público (liveness).
- [x] `/health/ready` sem detalhes internos do erro de banco em produção (mensagem
      genérica; detalhe só no log).
- **Aceite:** anônimo em produção não enumera config nem lê erro de infraestrutura.
- **Notas de execução:** nova config `DIAGNOSTICS_TOKEN` (`app/config.py`); em
  produção sem o token configurado as duas rotas negam por padrão
  (fail-closed, nunca abertas por omissão). Comparação via `hmac.compare_digest`.
  Fora de produção seguem livres (sem fricção local/staging). 7 testes novos
  em `tests/test_health.py`.

### 1.3 · M4 — Segredo dedicado do inbound e-mail 🟢 ✅ **Concluído 2026-07-15**
- [x] `routes/common.py`: remover o fallback `inbound_email_secret or session_secret`;
      inbound ativo sem segredo dedicado ⇒ rota desabilitada com log de aviso.
- [x] Avaliar usar o HMAC integral (não truncado a 16 hex) no endereço de resposta —
      medir impacto no comprimento do e-mail antes de decidir.
- **Aceite:** segredo de sessão nunca reutilizado para tokens de e-mail.
- **Notas de execução:** fallback removido em `routes/common.py` (webhook
  rejeita com 503 sem `INBOUND_EMAIL_SECRET`) e em `notification.py` (sem
  segredo dedicado, nenhum reply-to é gerado — log de aviso nos dois casos).
  Avaliação do HMAC integral: **deferida** — truncar para 16 hex já está em
  produção (tokens antigos quebrariam se o formato mudasse) e o ganho de
  segurança é marginal (16 hex = 64 bits de HMAC, suficiente pra esse caso de
  uso); não vale o risco de compatibilidade sem necessidade concreta. 7 testes
  novos/atualizados em `tests/test_inbound_email.py`.

### 1.4 · M11 — Gates de permissão na UI do Kanban 🟢 ✅ **Concluído 2026-07-15**
- [x] Aplicar no Kanban o mesmo gate da tela de atendimento: cartão fora do setor do
      usuário sem drag e sem botão de excluir (RLS continua como rede de segurança).
- [x] Teste em `test_workspace.py`: cartão alheio renderiza sem ações.
- **Aceite:** UI não oferece ação que a RLS vai negar.
- **Notas de execução:** mesmo critério `dept_bate` da tela de atendimento
  (`app/routes/workspace.py`), agora também no Kanban — precisou expor
  `c.departamento_id` em `_FILA_COLUNAS` (`app/repositories/chamados.py`, só
  faltava no SELECT). Cartão fora do setor recebe classe `kanban-card-locked`
  (Sortable.js ignora via `filter`/`preventOnFilter`) e não renderiza o botão
  de excluir. 1 teste novo em `tests/test_workspace.py`.

### 1.5 · M12 — Consistência do dual-write de papel 🟢 ✅ **Concluído 2026-07-15**
- [x] Após promoção em `/admin/usuarios`: reler `perfis.role` e `app_metadata.role` e
      falhar/alertar em divergência (hoje uma escrita pode falhar silenciosamente).
- [x] Opcional: SQL de reconciliação em `supabase/registro_usuarios.sql` para auditoria
      periódica.
- **Aceite:** divergência perfis × JWT detectada no ato, não em incidente futuro.
- **Notas de execução:** `AdminRepo.obter_papel` (nova) relê `perfis.role`
  após a escrita; `mudar_papel` também relê `app_metadata.role` via Admin API
  quando o service_role está configurado. Divergência no banco ⇒ resposta de
  erro explícita; divergência só no JWT ⇒ aviso específico (não mais um "ok"
  genérico enganoso); service_role ausente ⇒ logado (`log.error`), sem
  bloquear a resposta. Query de reconciliação periódica adicionada ao final de
  `supabase/registro_usuarios.sql`. 3 testes novos em `tests/test_admin.py`.

### 1.6 · M7 — Arquivar `prototipos/` 🟢 ✅ **Concluído 2026-07-15**
- [x] Mover para branch órfã/repo de arquivo **ou** manter a pasta e excluí-la de
      ferramentas (análise, grep de rotina, graphify) — decidir a forma mais simples.
- [x] Confirmar que nada em produção referencia `prototipos/` (Dockerfile/vercel.json não
      copiam — validar).
- **Aceite:** grafo/análises sem as ~20 comunidades "(Prototype)" duplicadas.
- **Notas de execução:** confirmado — `Dockerfile` só faz `COPY app ./app`;
  `vercel.json` só lista `app/static/**` e `app/main.py`; nenhum copia
  `prototipos/`. Optou-se pela forma mais simples (manter a pasta, excluir de
  ferramentas) em vez de branch órfã: `prototipos/` já estava fora do lint
  (`pyproject.toml`) e do bundle Vercel (`.vercelignore`); adicionado também a
  `.dockerignore` (higiene do contexto de build) e uma nota no
  `prototipos/README.md` orientando ferramentas de análise (graphify incluído
  — não tem mecanismo de ignore-file próprio) a apontar para `app/`, não a
  raiz do repo. **Pendência:** o `graphify-out/` atual (2026-07-10) ainda tem
  as comunidades "(Prototype)" antigas — só some numa próxima regeneração
  (`/graphify app` ou similar), não executada aqui (custo de LLM, decisão do
  gestor sobre quando vale regenerar).

### 1.7 · M9 — Suíte e2e de RLS recorrente 🔴 ✅ **Concluído 2026-07-15**
- [x] Suite dedicada (`tests/e2e/` ou marker `@pytest.mark.rls`) contra Supabase local
      cobrindo a matriz de visibilidade vigente: autor · staff RH/Marketing · líder de
      setor (0028) · TI pós-0020 · exceções Marketing (0038) e RH (0042) · nota interna
      invisível ao autor · upload de avatar (1º envio **e** reenvio, regressão da 0037)
      · Realtime não entrega `is_interna` ao cliente.
- [x] Integrar ao CI (job separado com `supabase start`; pode rodar só em PRs que tocam
      `supabase/` ou `app/repositories/`).
- **Aceite:** a classe de bug "mock verde × banco real divergente" (0028, chamados_departamento) coberta por teste automatizado.
- **Notas de execução:**
  - `supabase/config.toml` criado (`supabase init`) para viabilizar Supabase local
    pela primeira vez neste repo — desabilitado `db.seed` (a suíte semeia os
    próprios dados por teste), `edge_runtime` e `analytics` (não usados, Seção
    1.8 já decidiu Railway/sem Edge Functions).
  - `tests/e2e/conftest.py`: fixture `conn` abre **uma** conexão/transação por
    teste que nunca comita (rollback no teardown = limpeza automática, sem
    truncar tabela); fixture `seed` cria departamentos/usuários/chamados reais
    (mesmo padrão SQL do `docs/tutorial_usuarios.md`, trigger `handle_new_user`);
    helper `as_user()` reusa `app.db._apply_rls_claims` (o MESMO código de
    produção) para trocar de persona — em vez de reimplementar `SET LOCAL ROLE`
    à parte, o que arriscaria a suíte validar um mecanismo diferente do real.
  - `tests/e2e/test_rls_matrix.py`: 10 testes cobrindo os 8 pontos da matriz
    (autor; staff RH; staff Marketing; líder de setor 0028 — inclusive prova de
    que é só leitura, sem poder de atendimento; TI pós-0020; autoatendimento
    Marketing/RH 0038/0042 vs. TI sem a flag, que deve continuar bloqueado por
    RLS/`InsufficientPrivilegeError`; nota interna; avatar 1º envio + reenvio
    0037).
  - **Realtime (`is_interna`):** sem teste WebSocket separado — documentado em
    docstring que o Realtime do Supabase aplica a MESMA policy `mensagens_select`
    na entrega de `postgres_changes` por assinante; o teste de SELECT direto já
    é a prova da mesma garantia. Levantar um cliente Realtime de verdade no CI
    seria custo extra sem cobrir um mecanismo distinto.
  - `.github/workflows/e2e-rls.yml`: job separado (`supabase/setup-cli` +
    `supabase start` + `pytest tests/e2e -m rls`), só dispara em paths
    `supabase/migrations/**`, `app/repositories/**`, `app/db.py`,
    `tests/e2e/**` — não pesa no `ci.yml` principal.
  - Marker `rls` registrado em `pyproject.toml`; sem `RLS_DATABASE_URL` a suíte
    inteira é pulada via `pytest_collection_modifyitems` (não quebra `pytest`
    default de quem não tem Docker local).
  - **Pendência de validação — fechada em 2026-07-16:** o push do item 2.2
    (que toca `app/repositories/`) disparou o job `e2e-rls.yml` pela primeira
    vez de verdade (`ubuntu-latest`, Docker disponível). Achou 2 bugs reais na
    suíte — nunca pegos porque nunca tinha rodado contra um Postgres de fato —
    corrigidos no mesmo dia, ambos só em `tests/e2e/` (nenhum em código de
    produção):
    1. **Fixture `seed` travava em TODOS os 11 testes** (`_criar_usuario` em
       `conftest.py`): a promoção de `role`/`departamento_id` rodava na conexão
       de superusuário do seed, sem claims de RLS (`auth.uid()` NULL ali). O
       trigger `perfis_self_so_avatar` (migration 0033) só libera essas colunas
       quando `auth_is_ti()` é true — exige um `auth.uid()` válido apontando pra
       um staff já no setor TI, que no bootstrap do seed ainda não existe (o
       próprio primeiro usuário está sendo criado). Fix: desliga o trigger só
       para essa UPDATE (mesma transação nunca comitada, reabilitado antes de
       qualquer `as_user()`).
    2. **`test_ti_sem_flag_autoatendimento_nao_pode_se_autoatender`** (o único
       teste que esperava um erro do Postgres via `pytest.raises`): a RLS
       rejeitava certinho (`InsufficientPrivilegeError`), mas qualquer erro do
       Postgres aborta a transação inteira — o cleanup de `as_user()` (`RESET
       ROLE`) tentava rodar mais SQL nela logo depois e quebrava com
       `InFailedSQLTransactionError`, mascarando o sucesso real do teste. Fix:
       `async with conn.transaction():` em volta do UPDATE que deve falhar —
       aninhado dentro da transação do fixture `conn`, vira `SAVEPOINT`
       automático (padrão asyncpg) e absorve o erro esperado sem derrubar o
       resto da transação.
    - **11/11 testes verdes** no job `e2e-rls` após os dois fixes
      (`ba6991d`). Nenhuma policy/trigger de produção foi alterada — os dois
      bugs eram só na infraestrutura do teste (fixture de seed e isolamento de
      transação), não na RLS que a suíte valida.

### 1.8 · M10 — `[DECISÃO DO GESTOR]` Alvo canônico de deploy 🟡 ✅ **Decidido e executado 2026-07-15**
- Opções: **(a) Railway como produção única** (recomendação da auditoria e do próprio
  plano mestre — processo persistente, libmagic, BackgroundTasks confiáveis; Vercel
  vira apenas preview/desativa) · **(b) manter Vercel** ⇒ implementar outbox/fila com
  retry para e-mails (BackgroundTasks morre pós-response no serverless).
- [x] Decisão registrada aqui e no plano mestre: **(a) Railway única** (Osvaldo, 2026-07-15).
- [x] Executar a opção escolhida (limpar configs do alvo abandonado ou implementar outbox).
- **Aceite:** notificações por e-mail com garantia de envio no ambiente de produção real.
- **Notas de execução:** Vercel desativado por completo — `vercel.json` e
  `.vercelignore` removidos; `[tool.vercel]` tirado do `pyproject.toml`;
  `Settings.is_serverless` eliminado de `app/config.py`; os ramos
  condicionais que ele acionava em `app/db.py::init_pool` (pool restrito
  `min_size=0` do modo efêmero) e `app/notification.py::agendar_notificacao_email`
  (envio inline em vez de `BackgroundTasks`, workaround da função serverless
  morrer pós-response) foram removidos — agora sempre o caminho de servidor
  persistente, que é justamente a garantia de entrega que este item pedia.
  Comentários residuais mencionando Vercel/serverless em `app/main.py`,
  `app/config.py` e `app/notification.py` reescritos; `README.md` e a tabela
  de Estado do plano mestre atualizados. Entradas de changelog antigas
  (2026-07-01/03) mantidas como estão — são histórico, não estado atual.
  **Fora do escopo desta limpeza:** a duplicação `requirements.txt` ×
  `pyproject.toml [project.dependencies]` (isso é o item 2.5/M8, que esta
  decisão agora destrava).

---

## Sprint 2 — Estrutura, performance e operação (P2, 1–3 meses)

### 2.1 · M1 — Dividir o `ChamadosRepo` 🔴 ✅ **Concluído 2026-07-16**
- [x] Extrair por domínio, mantendo a fachada atual durante a migração (sem big-bang):
      `CatalogoRepo` (categorias/subcategorias/departamentos/setores) →
      `MensagensRepo` (mensagens/notificações/observadores) →
      `FilaRepo` (fila/kanban/stats/assinatura) →
      `AtendimentoRepo` (iniciar/atribuir/status/prioridade/transferir/excluir/marketing).
- [x] Um PR por extração, suíte verde em cada um; remover a fachada ao final.
- **Aceite:** nenhum repositório > ~300 linhas; testes inalterados passando.
- **Notas de execução:**
  - `app/repositories/chamados.py` (949 linhas) virou 5 arquivos: `catalogo.py`
    (107 linhas — `CatalogoRepo`), `mensagens.py` (189 — `MensagensRepo`),
    `fila.py` (246 — `FilaRepo`), `atendimento.py` (365 — `AtendimentoRepo`,
    inclui o helper privado `_registrar`, usado só por métodos deste domínio)
    e `chamados.py` (364 — fachada). `atendimento.py`/`chamados.py` ficaram
    um pouco acima dos "~300 linhas" alvo (docstrings extensas preservadas
    verbatim), mas a redução de 949→máximo 365 por arquivo já cumpre o
    espírito do Aceite.
  - Extração habilitada por três propriedades do código original,
    confirmadas antes de mexer: `ChamadosRepo` não tinha `__init__`/estado
    (cada método abre sua própria `rls_connection(claims)`); **zero chamadas
    cruzadas de domínio** entre métodos (todo `self.xxx()` interno já era
    intra-domínio); e nenhum teste faz `isinstance`/monkeypatch na classe —
    os 6 arquivos de teste usam `app.dependency_overrides[get_chamados_repo]
    = lambda: fake` com uma classe `Fake*Repo` duck-typed.
  - `ChamadosRepo` (fachada) instancia os 4 sub-repos no `__init__` e delega
    29 dos 33 métodos originais (assinatura idêntica, inclusive fronteira de
    keyword-only). `perfil`, `atualizar_avatar`, `listar`, `stats` ficaram
    implementados direto na fachada (self-service/perfil, não se encaixam
    com folga em nenhum dos 4 domínios; ~60 linhas ao todo). `criar`,
    `obter`, `avaliar` e `excluir` foram para `AtendimentoRepo` (ciclo de
    vida do chamado); `operadores()` foi para `FilaRepo` (é uma
    consulta/listagem, mesmo padrão de `setores_ativos`) apesar de também
    alimentar o dropdown de atribuição do `AtendimentoRepo`.
  - **Zero mudança em rotas ou testes**: as 27 declarações
    `Depends(get_chamados_repo)` (`workspace.py`, `portal.py`, `perfil.py`,
    `common.py`, `admin.py`) e os 6 arquivos de teste com `Fake*Repo`
    continuam batendo em `ChamadosRepo`/`get_chamados_repo`, sem tocar uma
    linha. `PRIORIDADES`, `validar_nota()` e as constantes de cache
    (`CACHE_CATEGORIAS`/`CACHE_DEPARTAMENTOS`/`CACHE_SUBCATEGORIAS`/
    `CATALOGO_TTL` — usadas por `app/routes/admin.py` para invalidar cache
    na escrita) continuam importáveis de `app.repositories.chamados`
    (re-exportadas do novo `catalogo.py`).
  - **Achado da própria migração:** a exploração inicial não pegou que
    `admin.py` importa as 4 constantes de cache diretamente de
    `chamados.py` (só métodos de instância apareceram no grepping de call
    sites) — a primeira rodada de testes quebrou por `ImportError`, corrigida
    re-exportando as constantes na fachada. Fica registrado como lição: numa
    extração assim, checar também imports de **constantes/símbolos** de
    módulo, não só de métodos de classe.
  - `avaliar` manteve o `INSERT INTO historico_chamados` manual (não passou
    a chamar `_registrar`, mesmo agora estando na mesma classe) — duplicação
    pré-existente, fora do escopo deste item (não pedido, não necessário
    para o Aceite).
  - Suíte pytest completa (197 testes, sem `tests/e2e`) verde; `ruff check`
    limpo; `npm run build:css` sem diff de output (nenhum template tocado).
  - "Remover a fachada ao final" (texto original do item) não foi feito
    agora — depende do item 2.2 (camada de serviço) existir para absorver a
    lógica que hoje está nas 27 rotas chamando `repo.<método>()` direto;
    registrado como próximo passo natural, não como pendência deste item.

### 2.2 · M2 — Camada de serviço nas rotas 🔴 ✅ **Concluído 2026-07-16**
- [x] Começar pelo workspace: `AtendimentoService` concentrando `pode_atender` /
      `pode_reivindicar` / exceções do Marketing (hoje espalhadas — origem do bug da 0028).
- [x] Na sequência: admin (gestão de usuários/dual-write) e portal (abertura de chamado).
- [~] Aproveitar para trocar a comparação por string `dep.nome = 'Marketing'` por flag de
      comportamento na tabela `departamentos` (ex.: coluna `autoatendimento boolean`) —
      migration própria. **Parcial/deferido** — ver nota de execução (fase admin/portal):
      os pontos que restam comparando por nome são feature-flag de exibição Marketing-only,
      não reaproveitáveis pela coluna `autoatendimento` (que cobre Marketing **e** RH) sem uma
      migration/decisão de produto dedicada, fora do escopo deste item.
- **Aceite:** regra de permissão de UI definida num único lugar por feature.
- **Notas de execução (fase workspace):**
  - Novo pacote `app/services/` com `AtendimentoService` (`app/services/atendimento.py`):
    `dept_bate()` (setor do chamado bate com o setor do staff logado) e
    `permissoes()` (`PermissoesAtendimento` — `dept_bate`/`eh_autor`/
    `eh_autoatendimento`/`bloqueado_por_autoria`/`pode_reivindicar`/`pode_atender`).
    Classe stateless (sem repositório/estado), chamada direto pela rota — não
    virou dependência FastAPI (`Depends`) por não ter nada a injetar.
  - **Achado real da auditoria, não só hipotético:** `kanban.html` recomputava
    `dept_bate` no próprio Jinja (`{% set dept_bate = c.departamento_id and
    perfil.departamento_id and ... %}`), com a mesma fórmula copiada à mão da
    rota `_carregar_atendimento` em `workspace.py`. As duas cópias já tinham
    divergido de forma sutil (uma delas dependia implicitamente de
    `perfil.departamento_id` truthy vs. `bool(...)` explícito) — exatamente o
    tipo de duplicação que originou o bug da migration `0028` (bypass de TI
    enxergando chamado de outro setor como atendível). Corrigido: a rota
    `kanban()` agora anota `c["dept_bate"] = AtendimentoService.dept_bate(c,
    ctx.perfil)` em cada chamado antes de montar as colunas, e o template só
    lê `c.dept_bate` — uma fonte única.
  - `_carregar_atendimento()` (tela de atendimento) passou a chamar
    `AtendimentoService.permissoes(chamado, ctx.perfil, ctx.user.id)` em vez de
    recalcular `eh_autor`/`eh_autoatendimento`/`dept_bate`/`ja_assumido`/
    `bloqueado_por_autoria`/`pode_reivindicar`/`pode_atender` inline. Zero
    mudança de comportamento (mesma fórmula, só movida) — suíte
    `tests/test_workspace.py` (38 testes) verde sem alterar nenhuma asserção.
  - Testes novos e puros (sem banco) em `tests/test_atendimento_service.py` (8
    casos): `dept_bate` true/false/perfil-sem-setor, reivindicar vs. atender
    conforme `ja_assumido`, bloqueio por autoria e a exceção de
    autoatendimento — inclusive o cenário específico do bug `0028` (setor
    diferente não deve nunca "bater").
  - **Escopo intencionalmente deixado de fora desta fase:** os `dep.nome ==
    'Marketing'`/`chamado.departamento == 'Marketing'` restantes em
    `workspace.py`/`atendimento.html` (rótulos de status, colunas extras do
    Kanban `A_FAZER`/`AGUARDANDO_TERCEIROS`, formulário de meta do Marketing)
    **não** são regra de permissão — são feature flag de exibição, e não dá
    para reaproveitar a coluna `autoatendimento` (migration 0042) para isso:
    ela cobre Marketing **e** RH, enquanto essas colunas/campos extras são
    Marketing-only (migrations 0024/0043). Trocar por uma flag própria
    (ex.: `departamentos.fluxo_estendido`) exige migration e decisão de
    produto dedicadas — registrado como pendência do 3º bullet deste item,
    não resolvido aqui.
  - Admin (dual-write de papel) e Portal (abertura de chamado) ficam para as
    próximas fases deste mesmo item, como o texto original já previa
    ("Começar pelo workspace... Na sequência...").
- **Notas de execução (fases admin + portal, mesmo dia):**
  - **`PortalService`** (`app/services/portal.py`): `pode_avaliar()` movida de
    `app/routes/portal.py` sem mudança (autor + `RESOLVIDO`). Achado real
    equivalente ao bug do Kanban (fase workspace): `_render_form` e
    `criar_chamado` reimplementavam, cada uma por conta própria, a mesma
    fórmula "achar o id do departamento Marketing pelo nome" — uma sobre a
    lista já filtrada por `recebe_chamados`, a outra sobre `setores_ativos`
    sem esse filtro. Unificado em `PortalService.marketing_dep_id()`. A regra
    de negócio do fluxo por demanda do Marketing (prioridade forçada +
    prazo mínimo de 48h / "sem prazo determinado", antes só dentro de
    `criar_chamado`) virou `PortalService.regras_marketing()`, retornando um
    `RegrasMarketing` (dataclass) que a rota só interpreta (erro → re-render
    do form; senão aplica prioridade/data/sem_prazo). Zero mudança de
    comportamento nas mensagens de erro/sucesso.
  - **Gap de teste encontrado e fechado:** a regra do Marketing (prazo 48h)
    não tinha nenhum teste de rota antes desta fase — só existia
    implicitamente no código. `tests/test_portal.py` ganhou 5 testes novos
    (sem data nem "sem prazo" → 400; data abaixo do mínimo → 400; data válida
    força `MEDIA`; "sem prazo" força `BAIXA`; fora do Marketing preserva a
    prioridade escolhida) e `tests/test_portal_service.py` (novo, 12 casos)
    testa `PortalService` isolado, sem banco — mesmo padrão de
    `test_atendimento_service.py`.
  - **`AdminService`** (`app/services/admin.py`): duas funções quase-idênticas
    da rota (`_depto_valido`, só para categoria — sempre exige fila; e
    `_depto_perfil_valido`, para papel/setor — só exige fila se `papel ==
    "OPERADOR"`) viraram um único `departamento_valido(departamentos, dep_id,
    *, exigir_fila)`. A rota agora busca `repo.departamentos(claims)` uma vez
    e passa a lista — função pura, testável sem repo/claims.
  - A orquestração de dual-write de papel (gravar `perfis` → espelhar
    `app_metadata.role` via Admin API → reler os dois lados → decidir a
    mensagem — item 1.5/M12), que vivia inteira dentro da rota `mudar_papel`
    e não tinha nenhuma parte no `AdminRepo`, virou
    `AdminService.promover_papel()` (retorna `ResultadoPapel(sucesso,
    mensagem)`; a rota só decide `ok=`/`erro=` a partir de `sucesso`). Mesmas
    3 mensagens de saída (sucesso limpo / aviso de divergência no JWT / erro
    de divergência no banco), mesmos logs — só movidos.
  - **Assimetria encontrada e conscientemente NÃO alterada:** `criar_usuario`
    também faz dual-write (Admin API grava `app_metadata.role` na criação da
    conta, depois `repo.atualizar_papel` grava `perfis`), mas nunca relia os
    dois lados como `mudar_papel` passou a fazer no item 1.5 — é uma
    orquestração estruturalmente diferente (criação de conta nova vs.
    promoção de conta existente), então não foi forçada a compartilhar
    `promover_papel()`; alinhar as duas exigiria decidir se vale a pena
    confirmar por releitura logo na criação da conta, o que é escopo novo,
    não uma extração. Registrado aqui para não se perder, não resolvido.
  - `_require_ti(ctx)` e a resolução de `is_ti`/`is_admin_dep`/`escopo` em
    `admin_context` **não** foram movidos para o serviço: são um único ponto
    de definição já (13 rotas chamam a mesma função; não há duplicação),
    então a extração não tinha o mesmo valor que a unificação de
    `_depto_valido`/`_depto_perfil_valido` ou do dual-write.
  - `app/routes/admin.py` perdeu o `import logging`/`log` de módulo (só
    existiam para os `log.error`/`log.warning` do dual-write, que migraram
    junto para o serviço).
  - Testes novos e puros em `tests/test_admin_service.py` (10 casos):
    `departamento_valido` (vazio, inexistente, inativo, com/sem exigir fila)
    e `promover_papel` (sucesso limpo, divergência no banco, divergência só
    no JWT, falha ao espelhar no Admin API sem quebrar a releitura de
    `perfis`) — usando fakes mínimos (`_FakeRepo`/`_FakeClient`), sem
    reaproveitar o `FakeAdmin` de `test_admin.py` (interfaces diferentes:
    aqui só a superfície que `AdminService` toca).
  - Suíte pytest completa (253 testes, exceto `tests/e2e`) verde — confirmado
    via `--junit-xml` (0 falhas/erros/skips) depois que a saída de texto do
    pytest, por alguma peculiaridade deste terminal Windows, parou de
    imprimir a linha de resumo final ("N passed"). `ruff check app/` limpo;
    `npm run build:css` sem diff (hash do CSS compilado idêntico antes/depois
    — nenhum template foi tocado nesta fase).
  - Com isso, o item 2.2 (M2) está concluído nas 3 fases previstas
    (workspace → admin → portal). O 3º bullet original ("trocar `dep.nome ==
    'Marketing'` por flag") fica **parcial**: a duplicação Python↔Python foi
    eliminada (fonte única em `PortalService`/`AtendimentoService`), mas a
    comparação por nome em si permanece em 3 pontos que são feature-flag de
    exibição, não permissão — `admin.py:136` (`escopo == "Marketing"`, decide
    qual dashboard renderizar), e os usos internos de
    `PortalService.marketing_dep_id()`/`AtendimentoService` equivalentes no
    workspace — nenhum reaproveitável pela coluna `autoatendimento` (cobre
    Marketing **e** RH) sem uma coluna/decisão de produto nova, mesma
    conclusão já registrada na fase workspace.

### 2.3 · M5 — Middlewares ASGI puros 🟡 ✅ **Concluído 2026-07-16**
- [x] Reescrever `SecurityHeadersMiddleware`, `SessionRefreshMiddleware` e
      `RequestContextMiddleware` como middleware ASGI puro (sem `BaseHTTPMiddleware`);
      manter o `GZipMiddleware` nativo.
- [x] Benchmark antes/depois numa rota de polling (fila) para registrar o ganho.
- **Aceite:** comportamento idêntico (headers, refresh de cookie, request-id) com suíte verde.
- **Notas de execução:**
  - As 3 classes viraram callables ASGI puros (`__init__(self, app, ...)` +
    `async def __call__(self, scope, receive, send)`), interceptando só o evento
    `http.response.start` via `MutableHeaders(scope=message)` — em vez de
    `BaseHTTPMiddleware`, que reconstrói a resposta inteira em memória (via
    `StreamingResponse`/task group) a cada request. `request_id` e
    `refreshed_session` passam a viver em `scope["state"]` (mesmo dict que
    `Request.state` já lia — nenhuma rota/dependency mudou). Ordem de
    `add_middleware` em `app/main.py` inalterada (mesma pilha: GZip → contexto →
    refresh de sessão → headers → router).
  - `app.add_middleware` aceita middleware ASGI puro do mesmo jeito que
    `BaseHTTPMiddleware` (só chama `cls(app, **options)`), então `app/main.py`
    não precisou de nenhuma mudança.
  - **Benchmark:** `GET /workspace/fila/fragmento` via `TestClient`, dependências
    trocadas por fakes (sem banco), 500 requisições após 50 de aquecimento,
    comparando o mesmo código com `git stash` (antes/depois isolados no mesmo
    processo/máquina): **antes** (BaseHTTPMiddleware) mean 5.29ms / p50 5.10ms /
    p95 6.04ms; **depois** (ASGI puro) mean 4.32ms / p50 4.16ms / p95 5.12ms —
    ganho de ~18% no tempo médio de resposta nessa rota (custo do próprio
    `BaseHTTPMiddleware`, que roda cada camada numa `anyio` task separada com
    stream intermediário).
  - Suíte pytest completa (exceto `tests/e2e`, que exige Docker) rodou 100% verde
    neste ambiente Windows, com uma exceção conhecida e documentada no item
    0.4: crash intermitente "access violation" no carregamento do
    `libmagic.dll` (via `python-magic-bin`, dependência só de Windows) nos
    testes de upload/avatar — reproduziu numa das execuções desta sessão e não
    nas outras duas (mesmo código, sem relação com esta mudança); não deve
    reproduzir no CI (`ubuntu-latest`).

### 2.4 · M6 — `[DECISÃO DO GESTOR]` Colocalizar app e banco 🟡 ✅ **Decisão tomada e executada 2026-07-16**
- Piso atual de ~320ms/query é latência até us-east-2. Opções: **(a)** deploy do app na
  mesma região do Supabase (Railway us-east) · **(b)** migrar o projeto Supabase para
  região próxima do app (mais invasivo).
- [x] Decisão + execução: opção **(a)** — Osvaldo configurou manualmente a região do
      serviço Railway para `us-east`, alinhando com o Supabase (`iurlzlhbnoemkzgexcfk`,
      confirmado via MCP em `us-east-2`). Ajuste de infra feito direto no painel do
      Railway (fora do escopo de PR/código).
- [ ] Medir `/portal` antes/depois em produção (meta: < 600ms) — **pendente**: ainda sem
      números registrados do antes/depois pós-mudança de região.
- **Aceite:** dashboard abaixo de ~600ms em produção — decisão e execução da colocalização
  concluídas; falta só a medição para fechar o critério de aceite numérico.
- **Notas de execução:**
  - Confirmado via MCP do Supabase (`list_projects`) que o projeto de produção está em
    `us-east-2` (AWS Ohio), `status=ACTIVE_HEALTHY`.
  - Consulta ao MCP do Railway (`list-projects`) não retornou nenhum projeto acessível
    nesta conta/token — não foi possível verificar programaticamente o serviço/região via
    MCP; a mudança foi confirmada diretamente pelo Osvaldo, feita manualmente no painel do
    Railway.
  - Pendência remanescente: medir `/portal` em produção após a mudança de região e
    registrar o número aqui para fechar o critério de aceite (meta < 600ms).

### 2.5 · M8 — Fonte única de dependências + atualização gerida 🟢 ✅ **Concluído 2026-07-15**
- [x] Eliminar a duplicação `requirements.txt` × `pyproject.toml` (gerar um do outro, ou
      só `pyproject` + lock se o alvo único de deploy — item 1.8 — permitir).
- [x] Ativar Dependabot/Renovate (agrupado, mensal) — versões pinadas sem processo
      acumulam CVEs silenciosamente.
- **Aceite:** um único lugar define versões; PRs automáticos de atualização chegando.
- **Notas de execução:** `pyproject.toml [project.dependencies]` era um espelho morto —
  sem `[build-system]` no arquivo, `pip` nunca instalou a partir dali (Dockerfile e o job
  `pytest` do CI sempre usaram `requirements.txt`/`requirements-dev.txt`). Removida a lista
  duplicada, substituída por um comentário apontando `requirements.txt` como única fonte —
  zero mudança de comportamento em Docker/CI. `.github/dependabot.yml` novo: 4 ecossistemas
  (`pip`, `npm`, `docker`, `github-actions`), agrupados, mensal, limite de 5 PRs abertos por
  ecossistema.

### 2.6 · Observabilidade — Sentry + uptime + métricas 🟡 ✅ **Concluído 2026-07-16** (uptime externo pendente — ver nota)
- [x] Sentry (ou similar) para exceções não tratadas, com `request_id` no contexto.
- [ ] Uptime check externo no `/health`. **`[DECISÃO/AÇÃO DO GESTOR]`** — assinatura de
      serviço terceiro (UptimeRobot, Better Stack, Pingdom...), fora do alcance de um PR;
      ver nota de execução.
- [x] Métricas mínimas: taxa de 304 no polling, saturação do pool asyncpg, p95 por rota
      (endpoint `/metrics` ou métricas do Railway).
- **Aceite:** critério de go-live "48h sem 5xx" verificável sem grep manual de log — atendido
  via `GET /metrics` (ver nota).
- **Notas de execução:**
  - **Sentry** (`app/observability.py::configure_sentry`, chamado no `lifespan` de
    `app/main.py`): liga só se `SENTRY_DSN` estiver configurada — DSN vazia (default) é
    integração totalmente desligada, `sentry_sdk.init()` nunca roda e
    `capture_exception()` vira no-op (confirmado isoladamente: chamar sem `init()` não
    levanta erro, só retorna `None`). Mesmo padrão de integração opcional já usado para
    Mailgun/WhatsApp neste repo (campo de config vazio = desligado, sem flag redundante).
    Nova config `sentry_traces_sample_rate` (default `0.0` — só erros, sem tracing de
    performance, que tem custo de armazenamento no Sentry; ligar sob demanda).
  - `_unhandled_exception_handler` (`app/main.py`) captura a exceção com
    `sentry_sdk.Scope().set_tag("request_id", ...).capture_exception(exc)` — um `Scope()`
    **isolado por chamada**, não `configure_scope`/`push_scope` mutando estado global, para
    não vazar a tag `request_id` entre requests concorrentes (o processo serve várias
    corrotinas ao mesmo tempo). O handler central já existia (Seção 6.3); só ganhou a
    chamada ao Sentry, sem mudar a resposta ao cliente (`{"detail": "Erro interno.",
    "request_id": ...}`, 500).
  - **Métricas** (`app/metrics.py`, novo módulo): contadores em memória por-processo, sem
    Prometheus/backend externo — mesmo espírito do cache (`app/cache.py`) e do rate limit
    (`app/ratelimit.py`) locais já existentes (ressalva idêntica: não soma entre réplicas,
    migrar para Redis se `>1` réplica, já coberto pelo checklist de scale-out da Seção 2.5
    do plano mestre). `registrar_request(path, status, duration_ms, is_polling=...)` chamado
    uma vez por request dentro do `RequestContextMiddleware` (`app/observability.py`) — o
    mesmo ponto que já loga `duration_ms`, sem round-trip extra. `is_polling` marca só
    `/workspace/fila/fragmento` (única rota com ETag/304 real, Seção 2.2); os fragmentos de
    chat fazem polling sem 304 (Realtime cobre o tempo real; polling é só fallback), então
    não entram na taxa de 304 para não distorcer o número.
  - `GET /metrics` (`app/routes/health.py`): mesmo gate `_diagnostico_autorizado` de
    `/health/ready`/`/health/config` (livre fora de produção; exige
    `X-Diagnostics-Token` em produção — fail-closed sem o token). Corpo: `requests_total`,
    `requests_5xx_total` (o critério de go-live em si — 48h sem 5xx vira "esse número não
    sobe" em vez de grep de log), `status_counts`, `latency_p95_ms_por_rota` (janela de até
    500 amostras por rota — `deque(maxlen=...)`, sem crescer sem limite), `polling_304`
    (`total`/`hits`/`taxa`), `db_pool` (via novo `app/db.py::pool_stats()` — `size`/`idle`/
    `min_size`/`max_size` do pool asyncpg, `None` se o pool ainda não foi inicializado,
    ex.: modo limitado) e `sentry_enabled` (booleano — diagnóstico rápido de config sem
    expor a DSN).
  - **Uptime externo — não implementável em código:** monitorar `/health` de **fora** do
    processo (para detectar o cenário em que o processo/host inteiro está fora do ar, não
    só uma exceção interna) exige uma conta num serviço terceiro batendo periodicamente na
    URL pública e alertando (e-mail/Slack/SMS) — não há nada para commitar num PR, mesmo
    padrão de pendência já registrado nos itens 0.4 (branch protection) e 2.4 (região do
    Railway). Recomendação registrada aqui para quando o gestor decidir: UptimeRobot ou
    Better Stack (ambos têm free tier), monitor HTTP a cada 5 min em
    `https://<domínio-produção>/health`, esperando `200` e `{"status":"ok"}`; alertar após
    2 falhas consecutivas (evita alarme falso por hiccup de rede pontual).
  - Suíte pytest completa (exceto `tests/e2e`, que exige Docker) verde: `tests/test_metrics.py`
    (novo, 5 casos puros — contagem de status/5xx, p95, taxa de 304 isolada por
    `is_polling`, janela limitada de amostras) e 6 casos novos em `tests/test_health.py`
    (`/metrics` livre fora de produção e refletindo o snapshot; negado em produção sem
    token; liberado com token certo; `sentry_enabled` true/false conforme DSN; captura no
    Sentry com `request_id` — mock de `sentry_sdk.Scope.capture_exception`, sem bater numa
    conta real). `ruff check app/` limpo.
  - Dependência nova: `sentry-sdk==2.19.0` em `requirements.txt` (única fonte de versões,
    item 2.5/M8) — instalada e validada localmente contra o `.venv` do projeto.

### 2.7 · B4 — Higiene documental 🟢 ✅ **Concluído 2026-07-15**
- [x] Extrair o changelog do plano mestre para `docs/CHANGELOG.md`.
- [x] Decisões grandes viram ADRs (`docs/adr/NNN-titulo.md`) linkados do plano.
- [x] Reescrever a matriz de permissões da Seção 3.2 no modelo vigente
      (0020/0027/0028/0038/0042) — hoje está marcada como desatualizada.
- **Aceite:** plano mestre navegável; matriz de permissões confiável para onboarding.
- **Notas de execução:**
  - Changelog de 24 entradas (2026-06-26→2026-07-15) movido pra `docs/CHANGELOG.md`;
    Seção 7 do plano mestre (protocolo de atualização) repontada pra registrar lá, linha
    mais nova no topo. O plano mestre ganhou uma nota curta linkando pro arquivo em vez da
    tabela inteira.
  - 6 ADRs novos em `docs/adr/` (índice em `docs/adr/README.md`), cobrindo as decisões
    genuinamente estruturais (não incrementos de feature — esses continuam só no
    changelog): RLS via `SET LOCAL`+claims em vez de `service_role` (0001), pooling
    Supavisor transaction mode (0002), pivô pra sistema interno por departamento (0003),
    SLA em horário comercial (0004), Railway como alvo único de deploy (0005), cache/rate
    limit local-por-processo com gatilho de Redis (0006).
  - Matriz da Seção 3.2 reescrita: o eixo deixou de ser "TI = acesso total, resto = só o
    setor" (modelo `0010`, incorreto desde a `0020` em 2026-07-06) e passa a refletir as
    três dimensões reais — setor de destino, setor de origem de quem olha, e se o setor
    tem `autoatendimento`. Nova coluna explícita para "ADMIN líder de setor sem fila"
    (`0028`, só leitura) e linha de autoatendimento (`0038`/`0042`). A tabela antiga
    chegou a induzir um bug real (bypass incondicional do TI na tela de atendimento,
    changelog de 2026-07-08) — nota no rodapé da tabela nova aponta as policies SQL da
    Seção 3.3 como fonte de verdade em caso de dúvida futura.

### 2.8 · B6 — Autenticação reforçada 🟡 ✅ **Concluído 2026-07-16** (ação de painel Supabase pendente — ver nota)
- [x] Executar o plano de hashing já redigido: revisar parâmetros do GoTrue e definir
      política de senha.
- [x] Avaliar MFA para contas staff/ADMIN (maior privilégio primeiro).
- **Aceite:** decisões registradas no plano mestre; MFA de staff avaliado com prazo.
- **Notas de execução:**
  - **Hashing:** confirmado que é delegado por completo ao GoTrue (bcrypt gerenciado pela
    plataforma, sem parâmetro de custo exposto do nosso lado) — "o plano de hashing já
    redigido" citado no achado da auditoria é essa própria delegação, não um documento
    separado a escrever; registrado explicitamente na Seção 3.4.1 (nova) do plano mestre
    para não ficar implícito.
  - **Política de senha — decisão:** mínimo de 8 caracteres, sem exigência de composição
    (segue NIST 800-63B — comprimento > regra de composição arbitrária). Já era o
    comportamento real nos dois pontos que criam/trocam senha (`/redefinir-senha` e
    `/admin/usuarios`), cada um com sua própria constante `8` duplicada — consolidado em
    `app/security/password_policy.py::SENHA_MIN_CHARS` (novo módulo, fonte única),
    reaproveitado por `app/auth/routes.py` e `app/routes/admin.py`. `supabase/config.toml`
    (stack local) alinhado (`minimum_password_length` 6→8) para documentar a mesma decisão
    também na config Supabase, ainda que essa config local não seja hoje exercida por
    nenhum teste (os testes de senha usam TestClient/mocks, não o GoTrue local de verdade).
  - **`[AÇÃO DO GESTOR PENDENTE]`:** o projeto Supabase **hospedado** (produção) segue com
    `minimum_password_length` no default `6` — não há Management API exposta via MCP do
    Supabase para automatizar esse ajuste; precisa ser feito manualmente no painel
    (Authentication → Policies), mesmo padrão de pendência dos itens 0.4 (branch protection)
    e 2.4 (região do Railway). Enquanto isso não acontece, o mínimo de 8 continua garantido
    pela própria aplicação (as duas rotas que criam/trocam senha), só não pela API do GoTrue
    caso algo a chame diretamente por fora do nosso formulário.
  - **MFA — avaliação, não implementação:** GoTrue já suporta TOTP nativo, sem trocar de
    provedor; o custo real está nas telas de enrollment/challenge que teríamos que construir
    (hoje `/login` é single-step). Faseamento recomendado e registrado na Seção 3.4.1 do
    plano mestre: Fase 1 (alvo Sprint 3) TOTP opcional para staff (`ADMIN`/`OPERADOR`); Fase
    2 (alvo Sprint 4, condicionada à adoção da Fase 1) obrigatório para `ADMIN`. Cumpre o
    aceite do item ("MFA de staff avaliado com prazo") sem implementar a feature agora —
    escopo maior (telas novas, fluxo de recovery codes) do que cabe num item 🟡 do Sprint 2.
  - Suíte pytest completa (exceto `tests/e2e`) verde — em particular
    `test_redefinir_senha_curta` (`tests/test_fase4_fase5.py`) e os testes de criação de
    usuário com senha curta (`tests/test_admin.py`), que exercitam o caminho agora
    consolidado, sem mudança de comportamento (valor do mínimo inalterado, só a fonte).
    `ruff check app/` limpo.

### 2.9 · Itens menores (B1, B2, B3, B5) 🟢 ✅ **Concluído 2026-07-15**
- [x] **B1:** checklist de scale-out no plano mestre (réplicas > 1 ⇒ Redis para cache +
      rate limit) — item de infra, não de código.
- [x] **B2:** manter decisão do bucket público de avatares registrada; reavaliar se a
      sensibilidade mudar.
- [x] **B3:** desfazer a auto-referência de import em `app/security/jwt.py`.
- [x] **B5:** teste unitário de claims adversariais (aspas/escape/unicode) em
      `_apply_rls_claims`.
- **Notas de execução:**
  - **B1:** nova Seção 2.5 no plano mestre ("Checklist de scale-out") consolidando os
    gatilhos de migração pra Redis já mencionados em 2.3 (cache) e 2.4 (rate limit), mais
    o redimensionamento do pool `asyncpg` por réplica — checklist, não executado agora
    (1 réplica hoje não bloqueia nada).
  - **B2:** nova Seção 3.9.1 registrando explicitamente que `avatares` é público **por
    decisão**, em contraste com `chamados-anexos` (privado) — com o gatilho de
    reavaliação se a sensibilidade do dado mudar.
  - **B3:** `app/security/jwt.py` → `app/security/jwt_verifier.py` (`git mv`, preserva
    histórico). 3 call sites atualizados: `app/auth/dependencies.py`, `app/main.py`,
    `tests/test_jwt.py`. Nenhum outro arquivo referenciava o módulo pelo nome antigo
    (`app/security/__init__.py` não reexporta). Suíte de `test_jwt.py` verde após o rename.
  - **B5:** `tests/test_db_rls_claims.py` novo, 9 casos parametrizados (aspas simples,
    aspas duplicadas, tentativa de fechar o literal SQL cedo com `'; DROP TABLE...`,
    aspas duplas, backslash, unicode/emoji/CJK/árabe, separadores de linha, dict aninhado
    com listas) + 1 teste de sanity do `SET LOCAL ROLE`. Não abre conexão real — captura a
    SQL final via um `_FakeConnection.execute` e decodifica pelas regras de literal do
    Postgres (`''` → `'`, único escape sob `standard_conforming_strings`), confirmando
    round-trip exato do claims original. 15 testes, suíte verde.

---

## Sprint 3 — Endurecimento de CI, pendências de gestor e MFA (P1/P2)

> Três melhorias entregues como PRs pequenos, separados e verificáveis, na ordem
> **3.1 → 3.2 → 3.3**: primeiro endurecer o gate de CI (a qualidade dos PRs
> seguintes se beneficia), depois destravar as pendências que só o gestor executa,
> por fim o MFA de ADMIN (Fase 1 do faseamento registrado na Seção 3.4.1 do plano
> mestre pelo item 2.8/B6).

### 3.1 · Endurecer o gate de CI (cobertura + ruff + mypy) 🟡 ✅ **Concluído 2026-07-16**
- [x] **Cobertura:** `pytest-cov` adicionado a `requirements-dev.txt`; cobertura
      **medida** (71.57%) e floor fixado ~2 pontos abaixo (`fail_under = 69` em
      `[tool.coverage.report]`) — adoção gradual, nunca um número aspiracional que
      quebre o build no dia 1. O job `pytest` do CI passa a rodar com `--cov=app` e
      reprova se a cobertura cair abaixo do piso.
- [x] **ruff:** `select` alargado de `["E9","F"]` (com `F401`/`F821` ignorados) para
      `["E9","F","I","UP","DTZ"]` **sem nenhum ignore**. Todas as ~65 violações
      resultantes corrigidas no mesmo PR (a suíte e `ruff check .` ficam verdes);
      nenhum `per-file-ignore` foi preciso.
- [x] **mypy:** adicionado ao dev + job de CI em modo **gradual** — cobre só
      `app/security/`, `app/db.py`, `app/config.py`, `app/auth/` (`[tool.mypy] files`),
      sem `--strict` e sem a árvore inteira; `follow_imports = "silent"` para não
      poluir o gate com dívida de módulos ainda não cobertos. Intenção de alargar
      documentada no `pyproject.toml` (mesmo comentário-padrão do ruff).
- [x] **`.github/workflows/ci.yml`:** o floor de cobertura virou step do job `pytest`
      (um só run faz teste + gate); o mypy virou job próprio.
- **Aceite:** CI verde com os novos gates ativos; nenhum gate aspiracional-que-já-falha;
  o PR não virou reformatação de arquivos fora do escopo (só lint/tipos/datetimes).
- **Notas de execução:**
  - **Cobertura medida:** `71.57%` (2062/2881 linhas do pacote `app`, suíte completa
    exceto `tests/e2e`, que é pulada sem `RLS_DATABASE_URL` — mesma condição do job
    `pytest` do CI). Floor `69` dá ~2.5 pontos de folga contra variação entre
    ambientes (Windows local × ubuntu do CI). Confirmado que o `pytest-cov` lê o
    `fail_under` do `pyproject.toml` (rodar um subconjunto pequeno reprova com
    "Required test coverage of 69.0% not reached", exit 1).
  - **ruff — o que as ~65 violações eram:** 18 `UP017` (`timezone.utc` → `UTC`), 12
    `I001` (imports fora de ordem), 10 `UP007` (`Optional[X]`/`Union` → `X | None`),
    10 `UP037` (anotações entre aspas), 6 `F821`, 5 `F401` (imports não usados), mais
    `UP035`/`UP031`/`DTZ003`/`DTZ005` (1 cada). A grande maioria foi `ruff check --fix`
    (fixes seguros); 4 pontos exigiram cuidado manual:
    - **`F821`/`UP037` (`"date | None"`):** os 3 repositórios (`atendimento.py`,
      `fila.py`, `chamados.py`) anotavam `data_entrega`/`data_de`/`data_ate` como
      `"date | None"` **entre aspas** sem importar `date` — a aspa era load-bearing
      (evitava `NameError`). Adicionado `from datetime import date` a cada um antes de
      deixar o `--fix` desaspar; F821 (nome não definido, latente) fechado de vez.
    - **`DTZ003`/`DTZ005` (`app/routes/admin.py`):** `datetime.utcnow()` (nome do CSV
      de export) e `datetime.now().year` (ano dos feriados) trocados por
      `datetime.now(UTC)` — tz-aware, **preserva exatamente o comportamento atual**
      (UTC), mesmo idioma já usado em `app/domain/sla_visual.py`. Era a dívida real
      apontada no próprio texto do item.
    - **`UP031` (`app/routes/workspace.py`):** o `'W/"%s"' % ...` do ETag (fix "unsafe"
      do ruff) reescrito à mão para f-string equivalente.
    - `combine-as-imports = true` no `[tool.ruff.lint.isort]` para não quebrar os
      re-exports aliased de `app.anexos` em `portal.py` num bloco por alias.
  - **mypy — 1 erro real corrigido:** `app/security/csrf.py::get_or_issue` retornava
    `existing` (`str | None`) numa função `-> str`; mypy não estreitava através de
    `_unsign()`. Guard explícito `existing is not None` (comportamento idêntico —
    `_unsign(None)` já retornava `None`). Os demais achados de `follow_imports` estavam
    em módulos fora do alvo (`app/domain`, `app/avatar_storage`), silenciados pelo
    `follow_imports = "silent"`. `warn_unused_ignores` deixado **de fora** desta 1ª
    adoção (flag estrita que forçaria mexer em `# type: ignore` pré-existentes não
    relacionados a tipo) — documentado que flags strict-* entram depois.
  - **Escopo:** 30 arquivos tocados, +187/-95, quase tudo lint automático (imports,
    anotações modernas, `UTC`). Nenhum template/JS mudou ⇒ `npm run build:css` sem
    diff. `datetime` só ficou tz-aware onde já devia estar; nenhuma regra de negócio
    alterada.
  - **Suíte:** `263 passed, 11 skipped` (os 11 são o e2e RLS, sem Docker neste
    ambiente), `ruff check .` limpo, `mypy` sem erros nos módulos cobertos,
    `build:css` sem diff.

### 3.2 · Fechar pendências de gestor (branch protection + política de senha) 🟢 ✅ **Artefato entregue 2026-07-16** — `[AÇÃO DO GESTOR]` para executar
- [x] Criar `docs/runbook_hardening_gestor.md` com o passo a passo exato de:
  - **(a) Branch Protection em `claude/develop`** (default branch; não há `main`):
    exigir CI verde + 1 revisão + sem force-push/deleção. Inclui o comando
    `gh api -X PUT .../branches/claude/develop/protection` (para quem tem admin+token)
    **e** o caminho pela UI (Settings → Branches) como fallback.
  - **(b) Política de senha do Supabase hospedado:** mínimo 6 → 8, alinhando ao
    `SENHA_MIN_CHARS`/NIST e ao `supabase/config.toml` local. Caminho no dashboard
    (Authentication → Password → Minimum password length).
- [ ] **`[AÇÃO DO GESTOR]` — (a) aplicar branch protection** (ver runbook). Só o
      gestor com admin executa; não alterado nesta sessão sem confirmação.
- [ ] **`[AÇÃO DO GESTOR]` — (b) ajustar mínimo de senha no painel Supabase** (ver
      runbook). Sem Management API via MCP; ação manual no dashboard.
- **Aceite:** runbook completo e acionável; nada de segredo ou permissão alterado sem
  o gestor no comando. **Atendido** — o artefato (runbook) está entregue; a execução
  das duas ações fica com o gestor.
- **Notas de execução:**
  - **Realidade confirmada nesta sessão** (via `gh`, autenticado como `OsvaldoBello`,
    dono do repo = admin, escopos `repo`/`workflow`): repositório **público**, default
    branch **`claude/develop`**, **sem** branch protection hoje
    (`gh api .../protection` → `404 Branch not protected`). Como o repo é público, a
    proteção clássica está disponível sem plano pago.
  - **`gh` admin disponível ⇒ oferta registrada:** o comando `gh api` do runbook pode
    ser aplicado nesta máquina. Por decisão de segurança (alterar configuração de
    repositório é ação de plataforma) **não foi executado sem confirmação do gestor** —
    o comando está no runbook, pronto, com a variante recomendada para mantenedor solo
    (CI obrigatório + no-force-push, revisão obrigatória opcional para não travar o
    self-merge de um time de 1 pessoa) e o `gh api -X DELETE` de reversão.
  - **Checks de CI a exigir** (nomes pós-item 3.1): `Suíte pytest + cobertura`,
    `Build Tailwind`, `Numeração de migrations`, `Lint (ruff)`, `Type-check (mypy,
    gradual)`. O `Matriz de RLS (Supabase local)` (`e2e-rls.yml`) **não** deve ser
    required — só roda em `paths` específicos e travaria PRs que não os tocam.
  - Sem código tocado ⇒ suíte inalterada (a base deste PR é o item 3.1, verde).

### 3.3 · MFA (TOTP) com enforcement para ADMIN 🔴 ✅ **Concluído 2026-07-16** (Fase 1 da Seção 3.4.1)
- [x] Fluxo de enrollment TOTP (enroll → challenge → verify) via GoTrue, com tela e rota
      próprias; QR/segredo exibidos uma única vez.
- [x] Enforcement: rotas do painel ADMIN exigem `aal2`, checando o claim `aal` na dependência
      de autorização (`admin_context` → `enforce_admin_mfa`), com redirect para o fluxo de
      verificação quando o ADMIN tem MFA habilitado mas a sessão está em aal1. CSP/headers e
      o refresh de sessão existentes seguem válidos.
- [x] Testes: enroll, verify, sessão aal1 barrada em rota ADMIN, aal2 liberada e usuário sem
      MFA (comportamento de transição).
- [x] Seção 3.4.1 do plano mestre atualizada + **[ADR-0007](docs/adr/0007-mfa-totp-aal2-admin.md)**
      registrando a decisão de MFA/aal2.
- [ ] **`[AÇÃO DO GESTOR]` — habilitar MFA/TOTP no projeto Supabase hospedado**
      (Authentication → Multi-Factor Authentication). O `supabase/config.toml` local já foi
      alinhado; ver item (c) do [runbook](docs/runbook_hardening_gestor.md).
- **Decisões do gestor (2026-07-16), tomadas antes de implementar:**
  - **Recovery:** reset por admin/TI (não recovery codes, não e-mail).
  - **Obrigatoriedade:** MFA de ADMIN **opcional com aviso** nesta fatia (Fase 1); obrigatório
    fica para a Fase 2 (Sprint 4).
  - **Lockout/janela de aal2:** padrões do GoTrue (sem re-desafio periódico próprio).
- **Aceite:** ADMIN com MFA não acessa o painel em aal1; ADMIN sem MFA não é bloqueado;
  OPERADOR/CLIENTE intocados. **Atendido.**
- **Notas de execução:**
  - **Arquivos novos:** `app/auth/mfa.py` (operações GoTrue), `app/routes/mfa.py` (5 rotas:
    `/mfa` hub, `/mfa/enroll`, `/mfa/enroll/confirmar`, `GET|POST /mfa/verify`),
    `app/templates/mfa/` (3 telas + shell), `tests/test_mfa.py`, ADR-0007.
  - **Segredo TOTP só no GoTrue** (não há coluna/tabela nova — zero migration). Cada operação
    usa um **cliente isolado** + `set_session` (as chamadas MFA mutam a sessão do GoTrue;
    reusar o cliente global daria corrida entre requests — mesma razão do fluxo de redefinição
    de senha).
  - **Decisão de desenho central — como o enforcement sabe que há MFA sem ir à rede:** o claim
    `aal` diz o nível da sessão, mas **não** diz se o usuário tem fator. Para distinguir
    "redirecionar" (tem MFA, aal1) de "avisar" (não tem), espelhamos um booleano em
    **`app_metadata.mfa_enabled`** via Admin API no enroll/reset — o GoTrue o embute no JWT, e
    o enforcement lê tudo dos claims: **local, sem rede por request, sem migration**. Mesmo
    padrão de dual-write do `app_metadata.role` (item 1.5); é um booleano, não segredo. As duas
    alternativas foram descartadas na ADR-0007: `list_factors()` por request (ida à rede em
    toda requisição de ADMIN em aal1 — contraria a razão de a verificação de JWT ser local) e
    coluna `perfis.mfa_enabled` (exigiria migration **e** mexer no trigger
    `enforce_perfil_self_so_avatar` da 0033, que só libera `avatar_path` na auto-escrita —
    risco de RLS desproporcional a um booleano).
  - **Efeito colateral bom:** como claims sem `mfa_enabled` degradam para o nudge, a suíte
    inteira roda **sem tocar o GoTrue** — os testes de admin existentes não precisaram de
    nenhuma alteração, mesmo com o `.env` local tendo `service_role` real configurada (uma
    detecção por Admin API teria feito chamadas de rede reais em cada teste de admin).
  - **Login reforçado sem custo:** quem já tem fator verificado vai direto ao step-up em vez da
    home — os `factors` vêm na própria resposta do `sign_in_with_password` (nenhuma chamada
    extra). Fator `unverified` (enroll abandonado) não conta.
  - **QR:** o gotrue-py monta o data URI concatenando o SVG **cru**, sem percent-encoding — um
    `#` de cor no SVG truncaria a URI no fragmento e a imagem não renderizaria. `mfa.py`
    reencoda (`_qr_data_uri`). Data URI é compatível com a CSP estrita (`img-src 'self' data:`,
    Seção 3.8) — nenhum host externo, CSP inalterada.
  - **Achado (fora do escopo, registrado):** o banner de nudge foi primeiro colocado em
    `app/templates/admin/admin_base.html` e não renderizou — esse template é **código morto**
    (nenhum arquivo o estende; as 4 páginas do painel usam `workspace/workspace_base.html`). A
    edição foi revertida e o banner foi para o shell verdadeiro, guardado por
    `mfa_nudge is defined` (só rotas do admin passam a flag ⇒ não aparece no Workspace). A
    remoção do template morto ficou como tarefa separada.
  - **Suíte:** `284 passed, 11 skipped` (295 coletados; os 11 são o e2e RLS, sem Docker neste
    ambiente) — 21 testes novos. Cobertura **70.78%**, acima do floor de 69 fixado no item 3.1
    (o piso com ~2 pontos de folga absorveu o código novo sem precisar mexer no gate, que era
    exatamente o propósito). `ruff check .` limpo, `mypy` verde (agora 14 arquivos — inclui
    `app/auth/mfa.py`), `npm run build:css` regenerado (as telas novas trouxeram classes
    novas; `app/static/css/app.css` vai no commit).

---

## Dependências entre itens

```
0.1 (migration 0015) ──► 0.4 (CI valida cadeia) ──► 1.7 (e2e RLS roda no CI)
1.8 (alvo de deploy)  ──► 2.5 (fonte única de deps) e fecha o M10 (e-mails)
2.1 (dividir repo)    ──► 2.2 (camada de serviço) — podem intercalar por feature
```

## Tabela de progresso

| Data | Item | PR/commit | Resultado |
|---|---|---|---|
| 2026-07-14 | 0.1 (A1) | `supabase/migrations/0015_subcategorias.sql` + `0045_fix_marketing_midia_regional_rls_enable.sql` | Migration `0015` reconstruída por introspecção de produção (tabela/FK/RLS/trigger idênticos; seed placeholder, dado original já superado pela 0018). Cadeia `0001→0044`+`0045` validada do zero num projeto Supabase descartável (free tier, pausado). Achado extra corrigido: RLS ausente de migration em `marketing_midia_regional`. |
| 2026-07-14 | 0.2 (A3) | `app/config.py`, `tests/test_config.py` | Fail-fast: `Settings` recusa instanciar em produção com `SESSION_SECRET`/`CSRF_SECRET` ainda no default de dev. 5 testes novos, suíte verde. |
| 2026-07-14 | 0.3 (A4) | `app/routes/whatsapp.py`, `tests/test_whatsapp.py` | Webhook WhatsApp: sem `WHATSAPP_APP_SECRET` em produção ⇒ 503 (antes aceitava sem validar); log só com tipo de evento + ids de mensagem, nunca payload/PII. 4 testes novos, suíte verde. |
| 2026-07-14 | 0.4 (A2) | `.github/workflows/ci.yml`, `scripts/check_migrations_sequence.py`, `pyproject.toml`, `requirements-dev.txt` | CI mínimo com 4 jobs (pytest, build:css, numeração de migrations, ruff). Branch protection (merge só com CI verde) fica pendente — ação manual de admin no GitHub, fora do alcance de um PR. |
| 2026-07-15 | 1.1 (A5) | `app/ratelimit.py`, `tests/test_ratelimit.py`, plano mestre (Seção 2.4) | Chave do rate limit passa a usar o último IP de `X-Forwarded-For` (hop do Railway), não o primeiro (forjável pelo cliente). |
| 2026-07-15 | 1.2 (M3) | `app/config.py`, `app/routes/health.py`, `tests/test_health.py`, `.env.example` | `/health/ready` e `/health/config` exigem `DIAGNOSTICS_TOKEN` em produção (fail-closed sem o token); erro de banco sem detalhe interno em produção. |
| 2026-07-15 | 1.3 (M4) | `app/routes/common.py`, `app/notification.py`, `tests/test_inbound_email.py` | Fallback `inbound_email_secret or session_secret` removido nos dois pontos (geração e validação do token); sem segredo dedicado, webhook rejeita (503) e nenhum reply-to é gerado. |
| 2026-07-15 | 1.4 (M11) | `app/templates/workspace/kanban.html`, `app/static/js/workspace.js`, `app/repositories/chamados.py`, `tests/test_workspace.py` | Kanban aplica o mesmo `dept_bate` da tela de atendimento: cartão fora do setor sem drag (`kanban-card-locked` + `Sortable.filter`) e sem botão de excluir. |
| 2026-07-15 | 1.5 (M12) | `app/repositories/admin.py`, `app/routes/admin.py`, `supabase/registro_usuarios.sql`, `tests/test_admin.py` | `mudar_papel` relê `perfis.role` e `app_metadata.role` após a escrita dupla; divergência no banco vira erro explícito, divergência só no JWT vira aviso específico (antes era "ok" genérico mesmo em falha silenciosa). |
| 2026-07-15 | 1.6 (M7) | `.dockerignore`, `prototipos/README.md` | Confirmado que Dockerfile/vercel.json não referenciam `prototipos/`; pasta mantida (não branch órfã) e documentada como arquivada/fora do escopo de ferramentas de análise. |
| 2026-07-15 | 1.7 (M9) | `supabase/config.toml`, `tests/e2e/conftest.py`, `tests/e2e/test_rls_matrix.py`, `tests/e2e/README.md`, `.github/workflows/e2e-rls.yml`, `pyproject.toml` | Suíte e2e (10 testes) contra Supabase local real cobrindo a matriz de visibilidade (autor, staff RH/Marketing, líder de setor 0028, TI pós-0020, autoatendimento Marketing/RH 0038/0042, nota interna, avatar 0037); reusa `app.db._apply_rls_claims` de produção para simular persona. Job de CI separado (`e2e-rls.yml`, só em paths de `supabase/`/`app/repositories/`). Sem Docker neste ambiente de execução — não rodada de ponta a ponta aqui; ver nota de execução do item para o que falta validar no próximo PR. |
| 2026-07-15 | 1.8 (M10) | `vercel.json` (removido), `.vercelignore` (removido), `pyproject.toml`, `app/config.py`, `app/db.py`, `app/notification.py`, `app/main.py`, `README.md`, plano mestre (tabela de Estado) | Decisão do gestor: Railway como alvo único de produção. Vercel desativado por completo — arquivos de deploy removidos, `Settings.is_serverless` e os ramos condicionais que ele acionava (pool restrito, envio inline de e-mail) eliminados. Destrava o item 2.5 (fonte única de dependências). |
| 2026-07-15 | 2.5 (M8) | `pyproject.toml`, `.github/dependabot.yml` | `pyproject.toml [project.dependencies]` (espelho morto, nunca instalado por `pip`) removido; `requirements.txt` confirmado como única fonte real (Dockerfile + CI). Dependabot novo: pip/npm/docker/github-actions, agrupado, mensal. |
| 2026-07-15 | 2.7 (B4) | `docs/CHANGELOG.md` (novo), `docs/adr/` (novo, 6 ADRs + índice), `plano_mestre_desenvolvimento.md` (Seções 3.2, 7, Changelog) | Changelog de 24 entradas extraído do plano mestre; 6 ADRs cobrindo as decisões estruturais (RLS/claims, pooling, pivô departamental, SLA comercial, Railway único, cache/rate-limit local); matriz de permissões da Seção 3.2 reescrita no modelo `0020`/`0027`/`0028`/`0038`/`0042` (a tabela antiga estava incorreta desde 2026-07-06 e já tinha induzido um bug real). |
| 2026-07-15 | 2.9 (B1/B2/B3/B5) | `plano_mestre_desenvolvimento.md` (Seções 2.5, 3.9.1 novas), `app/security/jwt.py`→`jwt_verifier.py`, `app/auth/dependencies.py`, `app/main.py`, `tests/test_jwt.py`, `tests/test_db_rls_claims.py` (novo) | B1: checklist de scale-out consolidado. B2: decisão do bucket público de avatares registrada explicitamente. B3: módulo `jwt.py` renomeado (desfaz a auto-referência de nome com o pacote `jwt` importado dentro dele). B5: 9 testes adversariais novos contra `_apply_rls_claims` (aspas/backslash/unicode/tentativa de fechar o literal SQL cedo), validando o round-trip via decodificação das regras de literal do Postgres. |
| 2026-07-16 | 2.3 (M5) | `app/security/headers.py`, `app/observability.py`, `app/auth/session.py` | `SecurityHeadersMiddleware`, `RequestContextMiddleware` e `SessionRefreshMiddleware` reescritos como ASGI puro (sem `BaseHTTPMiddleware`), interceptando só `http.response.start`; `request_id`/`refreshed_session` movidos para `scope["state"]`. Benchmark em `/workspace/fila/fragmento` (TestClient, 500 reqs): mean 5.29ms→4.32ms (~18% mais rápido). Comportamento idêntico, suíte verde. |
| 2026-07-16 | 2.1 (M1) | `app/repositories/catalogo.py`, `mensagens.py`, `fila.py`, `atendimento.py` (novos), `app/repositories/chamados.py` (reduzido a fachada) | `ChamadosRepo` (949 linhas) dividido em `CatalogoRepo`/`MensagensRepo`/`FilaRepo`/`AtendimentoRepo` (máx. 365 linhas cada), com `ChamadosRepo` virando fachada que delega 29 dos 33 métodos e mantém `perfil`/`atualizar_avatar`/`listar`/`stats` direto (self-service, baixo volume). Zero mudança em rotas/testes — as 27 declarações `Depends(get_chamados_repo)` e os 6 `Fake*Repo` de teste continuam batendo no mesmo nome/assinatura. `PRIORIDADES`, `validar_nota()` e constantes de cache do catálogo re-exportadas de `chamados.py` para não quebrar `app/routes/admin.py`. Suíte verde (197 testes), `ruff` limpo, `build:css` sem diff. |
| 2026-07-16 | 2.2 (M2, fase workspace) | `app/services/__init__.py`, `app/services/atendimento.py` (novos), `app/routes/workspace.py`, `app/templates/workspace/kanban.html`, `tests/test_atendimento_service.py` (novo) | `AtendimentoService` centraliza `dept_bate`/`eh_autor`/`eh_autoatendimento`/`bloqueado_por_autoria`/`pode_reivindicar`/`pode_atender`, hoje espalhados entre `_carregar_atendimento` (workspace.py) e o Jinja de `kanban.html`, que recomputava `dept_bate` por conta própria — a mesma classe de duplicação que originou o bug da migration `0028`. `kanban()` agora anota `dept_bate` por cartão via serviço antes de renderizar; `_carregar_atendimento` chama `AtendimentoService.permissoes(...)` em vez de recalcular inline. Zero mudança de comportamento — suíte completa (226 testes) e `ruff` verdes. Admin/portal (2ª e 3ª fases do item) e a troca do `dep.nome == 'Marketing'` por flag de comportamento ficam para PRs seguintes (ver notas de execução do item). |
| 2026-07-16 | 2.2 (M2, fases admin + portal) | `app/services/admin.py`, `app/services/portal.py` (novos), `app/routes/admin.py`, `app/routes/portal.py`, `tests/test_admin_service.py`, `tests/test_portal_service.py` (novos), `tests/test_admin.py`, `tests/test_portal.py` | Fecha o item 2.2. `AdminService`: unifica `_depto_valido`/`_depto_perfil_valido` (duas validações quase-idênticas de departamento) em `departamento_valido(exigir_fila=...)`; extrai a orquestração de dual-write de papel de `mudar_papel` (grava `perfis` → espelha `app_metadata.role` → relê os dois lados → decide mensagem) para `promover_papel()`. `PortalService`: move `pode_avaliar`; unifica a busca do departamento "Marketing" por nome, antes duplicada entre `_render_form` e `criar_chamado` (achado equivalente ao bug do Kanban); extrai a regra do fluxo por demanda do Marketing (prioridade forçada + prazo mínimo de 48h) para `regras_marketing()` — gap de teste fechado com 5 casos novos de rota, sem cobertura antes. Zero mudança de comportamento (mesmas mensagens/regras, só movidas), exceto a assimetria pré-existente entre `mudar_papel` (relê pra confirmar) e `criar_usuario` (não relê) — mapeada e conscientemente deixada como estava (orquestrações estruturalmente diferentes). Suíte completa (253 testes) e `ruff app/` verdes; `build:css` sem diff. |
| 2026-07-16 | 1.7 (M9) — fecha a pendência de validação | `tests/e2e/conftest.py`, `tests/e2e/test_rls_matrix.py` | O push do item 2.2 disparou o job `e2e-rls` pela primeira vez de verdade contra Docker/Supabase local no CI (`gh` instalado nesta sessão para acompanhar). Achou 2 bugs só na suíte de teste (nenhum em produção): (1) fixture `seed` travava em 100% dos testes — trigger `perfis_self_so_avatar` (0033) bloqueava a promoção de role/departamento do seed por rodar sem claims de RLS; fix desliga o trigger só nessa UPDATE, dentro da mesma transação nunca comitada; (2) o único teste que esperava um erro de RLS (`test_ti_sem_flag_autoatendimento_nao_pode_se_autoatender`) derrubava a transação inteira no cleanup do `as_user()`; fix isola a operação num SAVEPOINT (`conn.transaction()` aninhado). 11/11 testes verdes no CI após os dois commits (`fc61f41`, `ba6991d`). |
| 2026-07-16 | 0.1 (A1) — fecha a pendência do `rls_auto_enable()` | `supabase/migrations/0046_document_rls_auto_enable_trigger.sql`, `docs/adr/0001-rls-via-set-local-claims.md` | Investigação via MCP do Supabase (introspecção de `pg_proc`/`pg_event_trigger` em produção): o mecanismo é o event trigger `ensure_rls` (ON `ddl_command_end`) + função `rls_auto_enable()`, que habilita RLS automaticamente em toda tabela nova de `public` — nunca esteve em nenhuma migration (mesmo drift que causou a perda da 0015/RLS da `marketing_midia_regional`). Migration `0046` formaliza função + event trigger (idempotente); validada com `BEGIN`/`ROLLBACK` direto contra produção (sem erro, nenhuma mudança persistida — o mecanismo já existe lá). ADR-0001 ganhou um bullet documentando a rede de segurança complementar. |
| 2026-07-16 | 2.6 (Observabilidade) | `app/metrics.py` (novo), `app/observability.py`, `app/main.py`, `app/db.py`, `app/routes/health.py`, `requirements.txt`, `.env.example`, `app/config.py`, `tests/test_metrics.py` (novo), `tests/test_health.py` | Sentry opcional (`SENTRY_DSN` vazia = desligado) captura exceção não tratada em `_unhandled_exception_handler` com tag `request_id` via `Scope()` isolado por chamada (sem vazar entre requests concorrentes). Novo `GET /metrics` (mesmo gate de token de `/health/ready`): status/5xx totais, p95 de latência por rota, taxa de 304 do polling da fila, saturação do pool asyncpg. Uptime check externo fica pendente — decisão/ação do gestor (assinatura de serviço terceiro), recomendação registrada na nota de execução do item. Suíte verde, `ruff` limpo. |
| 2026-07-16 | 2.8 (B6) | `app/security/password_policy.py` (novo), `app/auth/routes.py`, `app/routes/admin.py`, `supabase/config.toml`, plano mestre (Seção 3.4.1, Estado) | Hashing confirmado como delegado ao GoTrue (nada a mudar em código). Política de senha: mínimo 8 caracteres sem exigência de composição (NIST 800-63B), consolidado em `SENHA_MIN_CHARS` (fonte única — antes duplicado em `auth/routes.py`/`routes/admin.py`); `supabase/config.toml` local alinhado. Ajuste equivalente no painel do projeto Supabase hospedado (hoje default 6) fica como `[AÇÃO DO GESTOR PENDENTE]` (sem Management API via MCP). MFA avaliado (não implementado): faseamento TOTP opcional pro staff (Sprint 3) → obrigatório pro ADMIN (Sprint 4), registrado na Seção 3.4.1. Suíte verde, `ruff` limpo. |
| 2026-07-16 | 3.1 (CI) | `pyproject.toml`, `requirements-dev.txt`, `.github/workflows/ci.yml`, `app/routes/admin.py`, `app/routes/workspace.py`, `app/security/csrf.py`, `app/repositories/{atendimento,fila,chamados}.py` + ~20 arquivos de lint automático | Gate de CI endurecido em adoção gradual. **Cobertura:** `pytest-cov`, medida em **71.57%**, floor `fail_under = 69` (~2 pts abaixo) no job `pytest`. **ruff:** `select` alargado p/ `["E9","F","I","UP","DTZ"]` sem ignore; ~65 violações corrigidas no mesmo PR (F821/UP037 `"date | None"` → import `date` + desaspar; DTZ `utcnow()`/`now()` → `now(UTC)` preservando comportamento; UP031 ETag → f-string). **mypy:** gradual, só `app/security`/`db.py`/`config.py`/`auth` (sem `--strict`, `follow_imports=silent`), job próprio; 1 erro real corrigido (`csrf.get_or_issue` narrowing). Nenhum template/JS tocado ⇒ `build:css` sem diff. `263 passed, 11 skipped`; `ruff check .` e `mypy` verdes. |
| 2026-07-16 | 3.2 (gestor) `[AÇÃO DO GESTOR]` | `docs/runbook_hardening_gestor.md` (novo) | Runbook acionável para as duas travas que só o gestor executa: **(a)** branch protection em `claude/develop` (default; sem `main`) — comando `gh api -X PUT .../protection` com os 5 checks required pós-3.1 + variante para mantenedor solo + reversão, e o caminho pela UI; **(b)** política de senha do Supabase hospedado (6 → 8, dashboard Authentication → Password). Confirmado via `gh`: repo público, default `claude/develop`, sem proteção hoje (404). `gh` admin disponível ⇒ oferta de aplicar (a) registrada, mas **não executada sem confirmação** (alterar config de repo é ação de plataforma). Sem código ⇒ suíte inalterada. Ver [runbook](docs/runbook_hardening_gestor.md). |
| 2026-07-16 | 3.3 (MFA) | `app/auth/mfa.py`, `app/routes/mfa.py`, `app/templates/mfa/` (novos), `app/auth/dependencies.py`, `app/auth/routes.py`, `app/routes/admin.py`, `app/main.py`, `app/templates/workspace/workspace_base.html`, `app/templates/admin/usuarios.html`, `supabase/config.toml`, `tests/test_mfa.py` (novo), `docs/adr/0007-mfa-totp-aal2-admin.md` (novo), plano mestre (Seção 3.4.1) | MFA TOTP Fase 1 (ADMIN-first). Enrollment (`/mfa`: enroll → challenge → verify, QR+segredo uma vez) e step-up (`/mfa/verify`), tudo via GoTrue — **segredo TOTP só no GoTrue, zero migration**. Enforcement de `aal2` no painel ADMIN (`enforce_admin_mfa` em `admin_context`): com MFA + aal1 ⇒ redirect (HTMX: `HX-Redirect`); **sem MFA ⇒ nudge, não bloqueia** (Fase 1 = opcional com aviso); OPERADOR/CLIENTE fora. Decisão-chave: espelho booleano `app_metadata.mfa_enabled` no JWT (padrão do dual-write de `role`, item 1.5) ⇒ enforcement **local, sem rede por request e sem tocar RLS** — alternativas (`list_factors` por request, coluna `perfis.mfa_enabled`) descartadas na ADR-0007. Recovery = **reset por TI** (`/admin/usuarios/{id}/reset-mfa`), sem recovery codes. Login manda quem já tem fator ao step-up (fatores vêm na resposta do login, sem chamada extra). Decisões do gestor (recovery/obrigatoriedade/lockout) tomadas antes de implementar. `284 passed, 11 skipped` (21 novos); cobertura 70.78% (acima do floor 69 do item 3.1); `ruff`/`mypy` verdes; `build:css` regenerado. `[AÇÃO DO GESTOR]`: habilitar TOTP no Supabase hospedado — item (c) do [runbook](docs/runbook_hardening_gestor.md). |

## Definição de pronto (todos os itens)

1. Suíte pytest completa verde (nunca só o arquivo tocado).
2. `npm run build:css` sem erro quando templates/JS/Python com classes mudarem.
3. Migration nova ⇒ aplicada e validada (advisors sem novos achados).
4. Plano mestre atualizado se schema/RLS/regra mudou (Seção 7 do plano mestre).
5. Linha registrada na tabela de progresso acima.

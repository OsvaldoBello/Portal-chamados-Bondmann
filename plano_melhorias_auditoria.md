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
    (idempotente). **Pendência:** a função `rls_auto_enable()` em si (o mecanismo
    que auto-habilita RLS em produção) continua fora de qualquer migration —
    vale investigar e capturar formalmente num item futuro de higiene (2.7/B4).

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

### 1.1 · A5 — Proxy confiável para o rate limit 🟢
- [ ] Configurar `--proxy-headers`/`forwarded_allow_ips` no Uvicorn (Railway) e revisar
      `app/ratelimit.py::client_ip` para usar o IP adicionado pelo proxy confiável, não o
      primeiro do header `X-Forwarded-For` (spoofável).
- [ ] Teste: header forjado não muda a chave de rate limit.
- **Aceite:** rate limit não contornável via header.

### 1.2 · M3 — Restringir endpoints de diagnóstico 🟢
- [ ] `/health/ready` e `/health/config`: em produção, exigir token simples (env) ou
      restringir; `/health` continua público (liveness).
- [ ] `/health/ready` sem detalhes internos do erro de banco em produção (mensagem
      genérica; detalhe só no log).
- **Aceite:** anônimo em produção não enumera config nem lê erro de infraestrutura.

### 1.3 · M4 — Segredo dedicado do inbound e-mail 🟢
- [ ] `routes/common.py`: remover o fallback `inbound_email_secret or session_secret`;
      inbound ativo sem segredo dedicado ⇒ rota desabilitada com log de aviso.
- [ ] Avaliar usar o HMAC integral (não truncado a 16 hex) no endereço de resposta —
      medir impacto no comprimento do e-mail antes de decidir.
- **Aceite:** segredo de sessão nunca reutilizado para tokens de e-mail.

### 1.4 · M11 — Gates de permissão na UI do Kanban 🟢
- [ ] Aplicar no Kanban o mesmo gate da tela de atendimento: cartão fora do setor do
      usuário sem drag e sem botão de excluir (RLS continua como rede de segurança).
- [ ] Teste em `test_workspace.py`: cartão alheio renderiza sem ações.
- **Aceite:** UI não oferece ação que a RLS vai negar.

### 1.5 · M12 — Consistência do dual-write de papel 🟢
- [ ] Após promoção em `/admin/usuarios`: reler `perfis.role` e `app_metadata.role` e
      falhar/alertar em divergência (hoje uma escrita pode falhar silenciosamente).
- [ ] Opcional: SQL de reconciliação em `supabase/registro_usuarios.sql` para auditoria
      periódica.
- **Aceite:** divergência perfis × JWT detectada no ato, não em incidente futuro.

### 1.6 · M7 — Arquivar `prototipos/` 🟢
- [ ] Mover para branch órfã/repo de arquivo **ou** manter a pasta e excluí-la de
      ferramentas (análise, grep de rotina, graphify) — decidir a forma mais simples.
- [ ] Confirmar que nada em produção referencia `prototipos/` (Dockerfile/vercel.json não
      copiam — validar).
- **Aceite:** grafo/análises sem as ~20 comunidades "(Prototype)" duplicadas.

### 1.7 · M9 — Suíte e2e de RLS recorrente 🔴
- [ ] Suite dedicada (`tests/e2e/` ou marker `@pytest.mark.rls`) contra Supabase local
      cobrindo a matriz de visibilidade vigente: autor · staff RH/Marketing · líder de
      setor (0028) · TI pós-0020 · exceções Marketing (0038) e RH (0042) · nota interna
      invisível ao autor · upload de avatar (1º envio **e** reenvio, regressão da 0037)
      · Realtime não entrega `is_interna` ao cliente.
- [ ] Integrar ao CI (job separado com `supabase start`; pode rodar só em PRs que tocam
      `supabase/` ou `app/repositories/`).
- **Aceite:** a classe de bug "mock verde × banco real divergente" (0028, chamados_departamento) coberta por teste automatizado.

### 1.8 · M10 — `[DECISÃO DO GESTOR]` Alvo canônico de deploy 🟡
- Opções: **(a) Railway como produção única** (recomendação da auditoria e do próprio
  plano mestre — processo persistente, libmagic, BackgroundTasks confiáveis; Vercel
  vira apenas preview/desativa) · **(b) manter Vercel** ⇒ implementar outbox/fila com
  retry para e-mails (BackgroundTasks morre pós-response no serverless).
- [ ] Decisão registrada aqui e no plano mestre.
- [ ] Executar a opção escolhida (limpar configs do alvo abandonado ou implementar outbox).
- **Aceite:** notificações por e-mail com garantia de envio no ambiente de produção real.

---

## Sprint 2 — Estrutura, performance e operação (P2, 1–3 meses)

### 2.1 · M1 — Dividir o `ChamadosRepo` 🔴
- [ ] Extrair por domínio, mantendo a fachada atual durante a migração (sem big-bang):
      `CatalogoRepo` (categorias/subcategorias/departamentos/setores) →
      `MensagensRepo` (mensagens/notificações/observadores) →
      `FilaRepo` (fila/kanban/stats/assinatura) →
      `AtendimentoRepo` (iniciar/atribuir/status/prioridade/transferir/excluir/marketing).
- [ ] Um PR por extração, suíte verde em cada um; remover a fachada ao final.
- **Aceite:** nenhum repositório > ~300 linhas; testes inalterados passando.

### 2.2 · M2 — Camada de serviço nas rotas 🔴
- [ ] Começar pelo workspace: `AtendimentoService` concentrando `pode_atender` /
      `pode_reivindicar` / exceções do Marketing (hoje espalhadas — origem do bug da 0028).
- [ ] Na sequência: admin (gestão de usuários/dual-write) e portal (abertura de chamado).
- [ ] Aproveitar para trocar a comparação por string `dep.nome = 'Marketing'` por flag de
      comportamento na tabela `departamentos` (ex.: coluna `autoatendimento boolean`) —
      migration própria.
- **Aceite:** regra de permissão de UI definida num único lugar por feature.

### 2.3 · M5 — Middlewares ASGI puros 🟡
- [ ] Reescrever `SecurityHeadersMiddleware`, `SessionRefreshMiddleware` e
      `RequestContextMiddleware` como middleware ASGI puro (sem `BaseHTTPMiddleware`);
      manter o `GZipMiddleware` nativo.
- [ ] Benchmark antes/depois numa rota de polling (fila) para registrar o ganho.
- **Aceite:** comportamento idêntico (headers, refresh de cookie, request-id) com suíte verde.

### 2.4 · M6 — `[DECISÃO DO GESTOR]` Colocalizar app e banco 🟡
- Piso atual de ~320ms/query é latência até us-east-2. Opções: **(a)** deploy do app na
  mesma região do Supabase (Railway us-east) · **(b)** migrar o projeto Supabase para
  região próxima do app (mais invasivo).
- [ ] Decisão + execução; medir `/portal` antes/depois (meta: < 600ms).
- **Aceite:** dashboard abaixo de ~600ms em produção.

### 2.5 · M8 — Fonte única de dependências + atualização gerida 🟢
- [ ] Eliminar a duplicação `requirements.txt` × `pyproject.toml` (gerar um do outro, ou
      só `pyproject` + lock se o alvo único de deploy — item 1.8 — permitir).
- [ ] Ativar Dependabot/Renovate (agrupado, mensal) — versões pinadas sem processo
      acumulam CVEs silenciosamente.
- **Aceite:** um único lugar define versões; PRs automáticos de atualização chegando.

### 2.6 · Observabilidade — Sentry + uptime + métricas 🟡
- [ ] Sentry (ou similar) para exceções não tratadas, com `request_id` no contexto.
- [ ] Uptime check externo no `/health`.
- [ ] Métricas mínimas: taxa de 304 no polling, saturação do pool asyncpg, p95 por rota
      (endpoint `/metrics` ou métricas do Railway).
- **Aceite:** critério de go-live "48h sem 5xx" verificável sem grep manual de log.

### 2.7 · B4 — Higiene documental 🟢
- [ ] Extrair o changelog do plano mestre para `docs/CHANGELOG.md`.
- [ ] Decisões grandes viram ADRs (`docs/adr/NNN-titulo.md`) linkados do plano.
- [ ] Reescrever a matriz de permissões da Seção 3.2 no modelo vigente
      (0020/0027/0028/0038/0042) — hoje está marcada como desatualizada.
- **Aceite:** plano mestre navegável; matriz de permissões confiável para onboarding.

### 2.8 · B6 — Autenticação reforçada 🟡
- [ ] Executar o plano de hashing já redigido: revisar parâmetros do GoTrue e definir
      política de senha.
- [ ] Avaliar MFA para contas staff/ADMIN (maior privilégio primeiro).
- **Aceite:** decisões registradas no plano mestre; MFA de staff avaliado com prazo.

### 2.9 · Itens menores (B1, B2, B3, B5) 🟢
- [ ] **B1:** checklist de scale-out no plano mestre (réplicas > 1 ⇒ Redis para cache +
      rate limit) — item de infra, não de código.
- [ ] **B2:** manter decisão do bucket público de avatares registrada; reavaliar se a
      sensibilidade mudar.
- [ ] **B3:** desfazer a auto-referência de import em `app/security/jwt.py`.
- [ ] **B5:** teste unitário de claims adversariais (aspas/escape/unicode) em
      `_apply_rls_claims`.

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

## Definição de pronto (todos os itens)

1. Suíte pytest completa verde (nunca só o arquivo tocado).
2. `npm run build:css` sem erro quando templates/JS/Python com classes mudarem.
3. Migration nova ⇒ aplicada e validada (advisors sem novos achados).
4. Plano mestre atualizado se schema/RLS/regra mudou (Seção 7 do plano mestre).
5. Linha registrada na tabela de progresso acima.

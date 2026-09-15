# Plano Mestre — Automação de Criação e Desligamento de Usuários via Chamados (RH → TI)

> **Documento vivo.** Governa a frente que liga o **Portal de Chamados** à **Automação de
> Representantes** (projeto CLI `Automação/`: MS Graph, UBD Learning.rocks, SAP Service Layer,
> WMW Vendas e CompanySIP via Playwright). Deve ser revisado ao final de cada PR desta frente
> (Seção 9).
>
> **Status:** 🟡 `F1, F2 e F3 em código (migration 0091 em produção; worker + Docker prontos, sem deploy) — F3a (túnel) e F4 a seguir` · **Criado em:** 2026-09-14 · **Atualizado em:** 2026-09-15
>
> **Relação com os outros docs:** convenções de stack, RLS, testes, deploy e a regra dura de
> segredos vêm do [`plano_mestre_desenvolvimento.md`](plano_mestre_desenvolvimento.md) e
> **prevalecem**. O projeto de automação tem seu próprio `PLANO_MESTRE_AUTOMACAO.md` (v1.0.0,
> 2026-08-04, "100% concluído") — este doc **não** o reescreve: descreve o que muda nele para
> rodar sem operador (Seção 6).

---

## Sumário

- [Seção 0 — Veredito de viabilidade e decisões pendentes](#seção-0--veredito-de-viabilidade-e-decisões-pendentes)
- [Seção 1 — Ponto de partida real (o que já existe dos dois lados)](#seção-1--ponto-de-partida-real-o-que-já-existe-dos-dois-lados)
- [Seção 2 — Onde a automação roda (Railway × on-prem)](#seção-2--onde-a-automação-roda-railway--on-prem)
- [Seção 3 — Arquitetura alvo](#seção-3--arquitetura-alvo)
- [Seção 4 — Formulários estruturados por subcategoria](#seção-4--formulários-estruturados-por-subcategoria)
- [Seção 5 — Modelo de dados e contrato da API do worker](#seção-5--modelo-de-dados-e-contrato-da-api-do-worker)
- [Seção 6 — Mudanças no projeto de automação](#seção-6--mudanças-no-projeto-de-automação)
- [Seção 7 — Segurança](#seção-7--segurança)
- [Seção 8 — Cronograma (F0–F5) com DoD](#seção-8--cronograma-f0f5-com-dod)
- [Seção 9 — Riscos, KPIs e protocolo de atualização](#seção-9--riscos-kpis-e-protocolo-de-atualização)
- [Tabela de Estado de Implementação](#tabela-de-estado-de-implementação)

---

## Seção 0 — Veredito de viabilidade e decisões pendentes

### 0.1 Veredito

**Viável, com uma restrição de rede que define a topologia.** Tudo do lado do portal
(formulários por subcategoria, fila de jobs, aprovação, retorno como nota interna) é trabalho
incremental sobre padrões que o repositório já tem (Seção 1). Do lado da automação, os
orquestradores `run_user_creation_flow` / `run_user_offboard_flow` já são desacoplados do CLI
e já degradam para `SKIP` quando não há TTY — o que falta é um *worker* que os alimente e
devolva o resultado.

A restrição: o **SAP Service Layer responde em IP privado (`10.151.4.40:50000`)**. Um
container no Railway **não alcança** esse endereço sem túnel. Os demais alvos (MS Graph, Skore,
CompanySIP e WMW em IP público `177.52.183.32:8081`) são alcançáveis da nuvem — WMW
possivelmente sujeito a allowlist de IP de origem (⚠️ a validar). Consequência: **ou o worker
roda dentro da rede da Bondmann, ou o Railway ganha um túnel até a rede** (Seção 2). O
desenho do portal é o mesmo nos dois casos, então o trabalho pode começar antes dessa decisão.

### 0.2 Decisões que precisam do gestor (bloqueiam fases específicas, não o início)

| # | Decisão | Recomendação | Bloqueia |
|---|---|---|---|
| **D1** | Onde o worker roda: (a) VM/servidor on-prem com Docker; (b) serviço no Railway + Tailscale *subnet router* on-prem; (c) SAP exposto publicamente (**descartado** — Seção 2). | **Decidido pelo gestor (2026-09-14): (b) — worker como segundo serviço no projeto Railway, alcançando o SAP por Tailscale.** Pré-requisito de infra na Seção 2.1. (a) fica como plano B com a mesma imagem. | F3/F4 |
| **D2** | Gate humano: execução automática na abertura ou só após um clique do TI ("Executar automação") no atendimento? | **Decidido pelo gestor (2026-09-14): gate obrigatório** nas duas subcategorias na v1**; auto-execução da *criação* fica atrás de env (`AUTOMACAO_AUTOEXEC_TIPOS`), como o padrão sombra→ativo da frente de IA. Desligamento é destrutivo (bloqueio, reset de senha, remoção de grupos, encaminhamento) — nunca sem gate. | F2 |
| **D3** | Quem vê o formulário estruturado: todos os autores nas duas subcategorias, ou só RH/TI/ADMIN? | **Decidido pelo gestor (2026-09-14): só RH, TI e ADMIN de setor.** Outros autores continuam no formulário livre e veem aviso "solicitações de criação/desligamento partem do RH". Motivo: campos como licenças SAP, papel no portal e motivo do desligamento não são de funcionário comum. | F1 |
| **D4** | Onde ficam as credenciais geradas (senha temporária M365, senha SIP, senha WMW)? | **Decidido pelo gestor (2026-09-14): mensagem pública de encerramento para o RH no chamado.** Quando o job termina sem pendência, o portal posta a mensagem final (e-mail criado, ramal, link WMW, senhas) visível ao autor (RH) e observadores e resolve o chamado; com pendência, posta o parcial sem "encerramento" e o TI fecha. **Cuidado obrigatório:** o e-mail de "nova mensagem" hoje reproduz o texto integral (`notification.py`) — a mensagem da automação dispara um e-mail **neutro** ("credenciais disponíveis no chamado BD-…", sem o conteúdo). M365 força troca no 1º login. | F2 |

### 0.3 Correções à premissa original (o que **não** vai ser feito como imaginado)

- **"Fazer uma requisição para rodá-la quando for criado o chamado"** (push do portal para a
  automação) vira **pull**: o worker pergunta ao portal se há job. Push exigiria porta de
  entrada na rede da Bondmann (ou no Railway) e retry/outbox do lado do portal; pull elimina
  os dois e é o mesmo padrão já usado na reconciliação da triagem (`_loop_reconciliacao`).
- **A etapa "Portal de Chamados (Supabase)" da automação sai do worker** e passa a ser feita
  pelo próprio portal (que já tem `admin_connection()` e o dual-write `perfis`+`app_metadata`
  de `supabase/registro_usuarios.sql`). Efeito colateral desejável: a `SUPABASE_SERVICE_ROLE_KEY`
  **deixa de existir** no `.env` da automação.
- **`offboarding_queue.json` e `automation.log` (arquivos locais) morrem**: filesystem de
  container é efêmero e ninguém olha o log. A fila de licenças de 15 dias vira um job agendado
  no banco do portal; log vai para stdout (Railway/Docker) + `resultado` jsonb do job.

---

## Seção 1 — Ponto de partida real (o que já existe dos dois lados)

### 1.1 Portal (este repositório)

| Peça | Onde | Reaproveitamento |
|---|---|---|
| Subcategorias-alvo já existem: **TI → "Usuários e Acessos" → "Criação de Novo Usuário"** e **"Desligamento / Bloqueio de Acesso"** | migration `0026` | Nenhuma migration de catálogo necessária. |
| Formulário dinâmico por categoria, com schema em código, cascata HTMX e gravação em `chamados.dados_formulario` (jsonb) | `app/domain/formularios_quimico.py`, `GET /portal/chamados/campos`, `portal/_campos_quimico.html`, `criar_chamado` em `app/routes/portal.py` | **É o mecanismo da Seção 4.** Precisa generalizar: chave por *subcategoria* (não só categoria), suporte a campo condicional (`visivel_se`), e tirar o `if eh_quimico` da rota. |
| Formulários obrigatórios do RH por subcategoria (download + bloqueio de conclusão) | `app/domain/formularios_rh.py`, `novo_chamado.js::aplicarFormularioObrigatorio` | Precedente de "comportamento por nome de subcategoria" e de **gate antes de concluir** (`formulario_pendente`). |
| Jobs assíncronos com reconciliação de órfãos e registro de auditoria | `app/ia/triagem.py` (`agendar_triagem`, `reconciliar_triagens_perdidas`), tabela `ia_triagens` (`0050`) | Modelo para a tabela `automacao_jobs` e para o loop de vigilância do worker. |
| Rota server-to-server com token comparado em tempo constante, fail-closed em produção | `app/routes/health.py::_diagnostico_autorizado` | Padrão de auth da API do worker (Seção 5.3). |
| Nota interna (`mensagens.is_interna`) e card de IA no atendimento | `workspace/atendimento.html` (`resumo_ia`, `ia_triagem`) | Onde entra o card "Automação de acessos" com status/botão. |
| Notificação por e-mail em BackgroundTasks | `app/notification.py` | Alerta "worker sem heartbeat" e "job falhou". |
| Regra dura de segredos, RLS + `SET LOCAL` claims, `admin_connection()` só server-side | plano mestre 3.1 e 6.2, ADR-0001 | Vale integralmente. |

### 1.2 Automação (`Automação/`, projeto separado)

- Entrada headless já existe: `AutomationFlowOrchestrator.run_user_creation_flow(UserCreationData)`
  e `run_user_offboard_flow(UserOffboardData)` retornam `list[StepResult]`; sem TTY, falhas viram
  `SKIPPED` (`_prompt_step_error_action`). Os `services/*` não usam `questionary`.
- Resolução automática de grupos M365 e times UBD por perfil/cargo/setor
  (`resolve_groups_by_job_title`, `resolve_teams_by_job_title`) — **não precisam virar campo de
  formulário**.
- Regiões WMW: `config/wmw_regions.json` (164 entradas `{value, label}`) + `utils/regions.py`.
  O portal já tem lista parecida em `formularios_quimico._REGIOES` (114); a fonte para o
  select do novo formulário é o JSON do WMW (é o que a automação valida).
- SAP já aponta para `SBO_BONDMANN_PRD` (o plano da automação dizia TST — homologação foi
  além do documentado; ⚠️ confirmar que criação de usuário SAP interno + licença está
  validada em PRD, não só em TST).
- Inventário de licenças SAP é consultado **ao vivo** na CLI (`get_license_inventory`). O portal
  não alcança o SAP (Seção 0.1) → o formulário oferece a lista fixa
  (PROFESSIONAL/CRM/FINANCEIRA/LOGISTICA/NENHUMA) e a disponibilidade é checada pelo worker
  na execução; sem estoque = etapa falha com mensagem clara, TI decide.
- ⚠️ O `.env` real (com credenciais de app-only do Entra ID, SAP, WMW, SIP e a service_role do
  Supabase) **está dentro do zip**. Não versionar; considerar rotação se o zip circulou fora
  da máquina do gestor. `config/settings.py` ainda tem usuário/senha do CompanySIP como
  *default* hard-coded — remover (Seção 7).

---

## Seção 2 — Onde a automação roda (Railway × on-prem)

| Critério | (a) Worker on-prem (Docker numa VM da Bondmann) | (b) Worker no Railway + túnel (Tailscale subnet router on-prem) | (c) Railway sem túnel |
|---|---|---|---|
| Alcança SAP `10.151.4.40` | ✅ nativo | ✅ via túnel (userspace networking, sem TUN — funciona em container comum) | ❌ **inviável** |
| Alcança WMW / SIP / Graph / Skore | ✅ | ✅ (WMW: allowlist do IP de saída do Railway, se houver — Pro tem IP estático) | parcial |
| Playwright/Chromium | ✅ VM comum | ✅ imagem `mcr.microsoft.com/playwright/python` (~1,5 GB, 512 MB–1 GB de RAM em execução) — cabe no plano pago | — |
| Segredos de alto privilégio (Graph `Directory.ReadWrite`) | ficam **dentro** da rede | ficam no Railway (env vars, como os do portal) | — |
| Operação (restart, logs, alerta) | **ponto fraco**: mesma classe de risco do wuzapi (sessão caída 11 dias sem ninguém ver) — mitigado por heartbeat + alerta por e-mail (Seção 5) | ✅ nativo do Railway | — |
| Custo/tempo até funcionar | dias (VM + Docker) | dias + configuração de rede (precisa de alguém da infra) | — |

**Decisão (D1, 2026-09-14): (b).** Expor o Service Layer na internet (mesmo com allowlist)
**não** entra como opção: é a porta de escrita do ERP. (a) permanece como plano B — a imagem
é a mesma, muda só onde sobe.

O Railway passa a hospedar dois serviços no mesmo projeto: o **portal** (já existe; guarda
**todo o estado**: fila, aprovação, notas, alertas) e o **`automacao-worker`** (novo, stateless,
sem domínio público, com suas próprias variáveis de ambiente).

### 2.1 Como o worker no Railway alcança o SAP (Tailscale)

```
Railway: automacao-worker                     Rede Bondmann
┌──────────────────────────────┐              ┌──────────────────────────────┐
│ tailscaled (userspace)       │  túnel       │ servidor "subnet router"      │
│   proxy local :1055 ─────────┼──WireGuard──▶│ tailscale --advertise-routes  │
│ worker.py                    │  (só saída,  │   =10.151.4.0/24              │
│   SAP  → via proxy :1055     │  sem porta   │        │ encaminha            │
│   Graph/Skore/WMW/SIP → direto│  aberta)    │        ▼                      │
└──────────────────────────────┘              │ SAP SL 10.151.4.40:50000      │
                                              └──────────────────────────────┘
```

**Lado Bondmann (infra, uma vez):**
1. Escolher um servidor que fica sempre ligado dentro da rede (Linux ou Windows) e instalar
   o Tailscale nele como **subnet router**: `tailscale up --advertise-routes=10.151.4.0/24`
   (Linux exige IP forwarding habilitado). Ele só faz conexão de **saída**; nenhuma porta de
   entrada no firewall.
2. No console admin do Tailscale: aprovar a rota anunciada; criar a tag `tag:automacao-worker`;
   ACL permitindo **somente** `tag:automacao-worker → 10.151.4.40:50000` (o worker não
   enxerga o resto da rede); gerar uma **auth key** *ephemeral + reusable + pre-authorized*
   com essa tag (nós efêmeros somem sozinhos a cada redeploy do Railway — sem lixo de
   dispositivos).
3. Opcional: se o firewall do WMW (`177.52.183.32`) só aceita o IP da Bondmann, anunciar
   também essa rota (`/32`) pelo mesmo subnet router — o tráfego do WMW passa a sair pela
   Bondmann e o allowlist continua válido sem depender de IP estático do Railway.

**Lado Railway (serviço `automacao-worker`):**
- Container comum, sem TUN nem `NET_ADMIN`: `tailscaled --tun=userspace-networking
  --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1055`, depois
  `tailscale up --authkey=$TS_AUTHKEY --hostname=automacao-worker --accept-routes`
  (`--accept-routes` é obrigatório em Linux para usar rotas de subnet). Se `tailscale up`
  falhar, o entrypoint sai com erro e o Railway reinicia o serviço — nunca roda "meio conectado".
- **Só o SAP usa o túnel**: `services/sap_api.py` passa a montar sua `requests.Session` com
  `proxies={"https": settings.SAP_PROXY_URL}` (`http://localhost:1055`). Graph, Skore e o
  Playwright (WMW/SIP) saem direto pela internet — não pagam a latência do túnel e não
  dependem do servidor on-prem.
- Envs novas: `TS_AUTHKEY` (segredo, rotacionável no console), `SAP_PROXY_URL`. Com chave
  efêmera não há estado do Tailscale para persistir (sem volume).
- Recursos: imagem Playwright ~1,5 GB; pico de RAM ~1 GB durante o scraping. Definir limite
  de memória do serviço acima disso no Railway.

**Consequência operacional:** o servidor subnet router vira dependência do passo SAP (só dele).
Se ele cair, as etapas SAP dão `FAILED`/`SKIPPED` e o resto do fluxo segue; a nota interna
diz "SAP inalcançável via túnel", e o `/saude` do worker reporta o estado do Tailscale para o
card. Monitorar esse servidor (é o único ponto on-prem do desenho).

---

## Seção 3 — Arquitetura alvo

```
RH/TI abre chamado (subcategoria Criação | Desligamento)
   │  formulário estruturado → chamados.dados_formulario
   ▼
Portal (Railway)  ── cria automacao_jobs(status=AGUARDANDO_APROVACAO, payload normalizado)
   │                 card no atendimento: [Executar automação]  ←  TI (gate D2)
   │                 → status NA_FILA (executar_apos = data informada no form, ou agora)
   ▼
Worker (on-prem / Railway)  ── POST /api/automacao/jobs/proximo   (claim atômico, SKIP LOCKED)
   │  roda flow.py            ── POST /api/automacao/jobs/{id}/heartbeat  (a cada etapa)
   │  step a step             ── POST /api/automacao/jobs/{id}/resultado  (lista de StepResult)
   ▼
Portal  ── grava resultado; nota interna com o detalhe técnico por sistema (só staff);
           sem pendência → mensagem pública de ENCERRAMENTO ao RH com as credenciais (D4)
           e status RESOLVIDO; com pendência → mensagem pública parcial, status
           EM_ATENDIMENTO e o TI conclui manualmente
        ── agenda job REVOGAR_LICENCA (executar_apos = desligamento + 15 dias)
        ── loop de vigilância: job EXECUTANDO sem heartbeat > N min → FALHOU + e-mail ao TI;
           worker sem `proximo` há > 30 min em horário comercial → e-mail "worker parado"
```

Regras:
1. **Um job ativo por chamado por tipo** (unique parcial). Reexecução cria job novo apontando
   para o anterior (`reexecucao_de`), só com as etapas que não deram `SUCCESS` (idempotência já
   existe nos serviços: `GET` antes de `POST` no Graph/Skore; no WMW/SIP, o worker checa
   existência).
2. **Chamado só é resolvido pela automação quando TODAS as etapas deram `SUCCESS`** (e não
   em dry-run). Qualquer `SKIPPED`/`FAILED` deixa o chamado em atendimento; o card mostra o
   que foi feito e o que ficou pendente para ação manual do TI. Reabertura pelo RH continua
   valendo como em qualquer chamado.
3. **Kill switch global** (`AUTOMACAO_ATIVA=false` → API responde vazio, cards mostram "pausado")
   e **por tipo** (`AUTOMACAO_TIPOS=CRIACAO,DESLIGAMENTO`).
4. **Dry-run de ponta a ponta**: job com `dry_run=true` roda o fluxo simulado da automação
   (já suportado) e devolve o resumo — é o modo da homologação (F4) e fica disponível como
   opção no card para o TI.

---

## Seção 4 — Formulários estruturados por subcategoria

### 4.1 Generalização do mecanismo do Químico `[DECISÃO DE ENGENHARIA]`

- Novo módulo `app/domain/formularios_dinamicos.py` com o registro
  `layout_para(departamento, categoria, subcategoria) -> tuple[CampoDef, ...]`; o Químico
  continua registrado por categoria, os dois novos por subcategoria. `CampoDef` ganha
  `visivel_se: tuple[str, tuple[str, ...]] | None` (campo condicional) e a validação ignora
  campos ocultos. `select` passa a aceitar opções vindas de função (setores ativos do portal).
- `GET /portal/chamados/campos` recebe também `subcategoria_id`; `novo_chamado.js` dispara a
  cascata na troca de subcategoria (hoje só na de categoria). Partial renomeado para
  `portal/_campos_dinamicos.html` (o Químico passa a usá-lo — sem mudança visual).
- Na rota `criar_chamado`, o bloco `if eh_quimico:` vira `if layout:` — mesma validação,
  mesmo `titulo_e_descricao_automaticos`. Para as duas subcategorias, Assunto/Descrição
  ficam ocultos e derivados (`"Criação de usuário — {nome} ({perfil})"`).
- Gate D3 aplicado no servidor (não só no JS): fora de RH/TI/ADMIN, o layout não é
  servido e `campo__*` é ignorado.
- Detalhe do chamado (`rotular`) e atendimento mostram os campos rotulados — já funciona
  para o Químico.

### 4.2 Campos — "Criação de Novo Usuário" (espelha `main.py::handle_create_user`)

| Campo (`name`) | Tipo | Obrigatório | Visível se | Fonte / regra |
|---|---|---|---|---|
| `nome_completo` | text | sim | — | — |
| `email` | email | sim | — | JS sugere `primeiro.ultimo@bondmann.com.br`; servidor exige domínio `@bondmann.com.br` |
| `perfil` | select | sim | — | `REPRESENTANTE` / `INTERNO` / `SUPERVISOR` (rótulos da CLI) |
| `telefone` | tel | sim | — | mesma normalização de `telefone_contato` |
| `data_inicio` | date | não | — | vira `executar_apos` (não executa antes do dia) |
| `gestor_email` | email | não | — | `manager_email` → `leaders` no UBD |
| `cargo` | text | sim | `perfil = INTERNO` | `job_title` (resolve times UBD e grupos M365) |
| `regiao_wmw` | select | sim | `perfil ∈ {REPRESENTANTE, SUPERVISOR}` | `config/wmw_regions.json` (copiado para o portal como dado estático versionado) |
| `dispositivo_wmw` | select | sim | `perfil = REPRESENTANTE` | IOS / ANDROID / SIMULADOR |
| `portal_papel` | select | sim | `perfil = INTERNO` | CLIENTE / OPERADOR / ADMIN (Representante e Supervisor: fixo CLIENTE + setor fixo, como na spec 2026-09-10 da automação) |
| `portal_setor` | select | sim | `perfil = INTERNO` | departamentos ativos (`setores_ativos`, já carregado na tela) |
| `licencas_sap` | checkbox_multi | não | `perfil = INTERNO` | PROFESSIONAL / CRM / FINANCEIRA / LOGISTICA; vazio = nenhuma |
| `observacoes` | textarea | não | — | vai só para o TI, não para a automação |

### 4.3 Campos — "Desligamento / Bloqueio de Acesso" (espelha `handle_offboard_user`)

| Campo | Tipo | Obrigatório | Visível se | Fonte / regra |
|---|---|---|---|---|
| `email` | email | sim | — | ⚠️ v2: autocomplete a partir de `perfis` (o portal tem todos os colaboradores) |
| `nome_completo` | text | sim | — | — |
| `perfil` | select | sim | — | REPRESENTANTE / INTERNO / SUPERVISOR |
| `data_desligamento` | date | sim | — | `executar_apos`; default hoje |
| `regiao` | select | sim | `perfil ∈ {REPRESENTANTE, SUPERVISOR}` | mesma lista WMW; região a transferir para RH2020 |
| `motivo` | text | sim | — | default "Encerramento de Contrato de Trabalho" |
| `encaminhar_para` | email | sim | — | default `pedidos@bondmann.com.br`; SDR adiciona o 2º destinatário na automação |
| `observacoes` | textarea | não | — | só TI |

### 4.4 O que **não** vira campo

Grupos M365, times UBD, senha inicial, código de usuário SAP (`primeiro nome`), ramal SIP
(sequencial) — tudo derivado pela automação. Menos campo = menos erro do RH.

---

## Seção 5 — Modelo de dados e contrato da API do worker

### 5.1 DDL (migration `0091_automacao_jobs.sql`) — atualizar a Seção 5 do plano mestre antes do código

```sql
CREATE TYPE automacao_tipo   AS ENUM ('CRIACAO', 'DESLIGAMENTO', 'REVOGAR_LICENCA');
CREATE TYPE automacao_status AS ENUM (
  'AGUARDANDO_APROVACAO', 'NA_FILA', 'EXECUTANDO',
  'CONCLUIDO', 'CONCLUIDO_COM_PENDENCIAS', 'FALHOU', 'CANCELADO');

CREATE TABLE automacao_jobs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chamado_id      uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  tipo            automacao_tipo NOT NULL,
  status          automacao_status NOT NULL DEFAULT 'AGUARDANDO_APROVACAO',
  dry_run         boolean NOT NULL DEFAULT false,
  payload         jsonb NOT NULL,                 -- dados normalizados (Seção 4), SEM segredos
  resultado       jsonb NOT NULL DEFAULT '[]',    -- lista de StepResult (senhas mascaradas)
  executar_apos   timestamptz NOT NULL DEFAULT now(),
  aprovado_por    uuid REFERENCES perfis(id),
  aprovado_em     timestamptz,
  worker_id       text,
  claimed_at      timestamptz,
  heartbeat_at    timestamptz,
  finalizado_em   timestamptz,
  tentativas      integer NOT NULL DEFAULT 0,
  reexecucao_de   uuid REFERENCES automacao_jobs(id),
  erro            text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_automacao_jobs_ativo
  ON automacao_jobs(chamado_id, tipo)
  WHERE status IN ('AGUARDANDO_APROVACAO','NA_FILA','EXECUTANDO');
CREATE INDEX idx_automacao_jobs_fila ON automacao_jobs(executar_apos) WHERE status = 'NA_FILA';

ALTER TABLE automacao_jobs ENABLE ROW LEVEL SECURITY;
-- SELECT: staff do departamento de destino do chamado (TI) e ADMIN — reusa auth_departamento_id()
-- INSERT/UPDATE: só via admin_connection() (portal); o worker NÃO fala com o banco.
GRANT SELECT ON automacao_jobs TO authenticated;
```

Credenciais geradas **não** entram em `payload`/`resultado`: a nota interna recebe a senha
temporária (D4) e o `resultado` guarda `"temp_password": "***"` (a automação já tem
`mask_sensitive_str`).

### 5.2 Transições

`[DECISÃO DE ENGENHARIA — F2]` "Aguardando aprovação" **não é uma linha**: o job só nasce
quando o TI clica (índice único parcial garante um ativo por chamado/tipo). Isso deixa a RLS
mínima (só o staff do destino insere/cancela; o worker escreve só pela conexão administrativa)
e dispensa o status `AGUARDANDO_APROVACAO` da DDL original.

`[TI clica] --> NA_FILA --[claim]--> EXECUTANDO --> CONCLUIDO | CONCLUIDO_COM_PENDENCIAS
(algum SKIPPED/FAILED) | FALHOU (exceção fora do fluxo / sem heartbeat / nenhuma etapa
SUCCESS)`. `CANCELADO` pelo TI enquanto `NA_FILA`. Job `REVOGAR_LICENCA` nasce direto em
`NA_FILA` com `executar_apos = offboard_date + 15 dias` (D2 não se aplica: é a regra interna já
vigente). Reexecução = job novo com `reexecucao_de` e `pular_etapas`.

**Agendamento (decisões do gestor, 2026-09-14):** criação às 07h (Brasília) do dia útil
anterior à `data_inicio` (feriados da tabela `feriados`); desligamento às 18h da
`data_desligamento`; sem data ou data passada = imediato; simulação (dry-run) = imediato.

**Conta no Portal de Chamados:** criada pelo próprio portal ao concluir uma CRIACAO
(GoTrue Admin + `perfis` sob a RLS do aprovador) — a etapa sai do worker
(`docs/automacao_api.md`).

### 5.3 API (rotas em `app/routes/automacao_api.py`, prefixo `/api/automacao`)

- Auth: header `X-Automacao-Token` comparado com `hmac.compare_digest` contra
  `AUTOMACAO_WORKER_TOKEN` (fail-closed: sem env, 404 em tudo). Rate limit slowapi. Sem cookie,
  sem CSRF (server-to-server, mesmo tratamento do inbound de e-mail).
- `POST /jobs/proximo` `{tipos: [...]}` (worker identificado pelo header `X-Automacao-Worker`) → claim atômico
  (`UPDATE ... WHERE id = (SELECT id ... FOR UPDATE SKIP LOCKED) RETURNING`) de 1 job com
  `status='NA_FILA' AND executar_apos <= now()`; 204 se vazio. Devolve `payload`, `tipo`,
  `dry_run`, `chamado.codigo`.
- `POST /jobs/{id}/heartbeat` `{etapa_atual}` → atualiza `heartbeat_at`; 409 se o job não
  estiver `EXECUTANDO` com este `worker_id` (job dado como morto e reatribuído).
- `POST /jobs/{id}/resultado` `{steps: [StepResult...], credenciais: {...}}` → transição
  final, nota interna técnica, mensagem pública ao RH (com `credenciais` quando concluído sem
  pendência — D4), `RESOLVIDO` ou `EM_ATENDIMENTO`, agendamento de `REVOGAR_LICENCA`, e-mail
  ao TI se houver pendência. O e-mail de "nova mensagem" gerado por essa mensagem é o
  neutro (sem conteúdo). `credenciais` **não** é persistido em `resultado`.
- `GET /saude` → usado pelo worker no boot para validar token e `versao_contrato` (e por
  `/health/ready` do portal para reportar "último worker visto há X min").

Contrato versionado em `docs/automacao_api.md` — é o único acoplamento entre os dois repos.

### 5.4 Vigilância (loop no lifespan, como `iniciar_reconciliacao`)

- `EXECUTANDO` sem heartbeat há > `AUTOMACAO_HEARTBEAT_TIMEOUT_S` (default 900) → `FALHOU`
  ("worker parou no meio"), e-mail ao TI, `tentativas += 1`; volta para `NA_FILA` automaticamente
  só se `tentativas < 2` **e** `tipo != DESLIGAMENTO` (desligamento pela metade exige olho
  humano antes de repetir).
- Nenhum `proximo` recebido há > 30 min em horário comercial (`app/domain/periodo.py`) com
  jobs `NA_FILA` → e-mail "worker de automação sem contato" (uma vez por hora, não em loop).

---

## Seção 6 — Mudanças no projeto de automação

1. **`worker.py`** (novo, ao lado de `main.py`): loop `while True` → `proximo` → monta
   `UserCreationData`/`UserOffboardData` a partir do `payload` (mapeamento 1:1 com a Seção 4;
   mesma lógica de `find_wmw_region`, sugestão de e-mail e defaults comerciais de `main.py`,
   extraída para `models/factories.py` para ser testável) → `orchestrator.run_*_flow` com
   callback de heartbeat por etapa → `resultado`. Backoff em erro de rede; `SIGTERM` limpo.
2. **`flow.py`**: aceitar `on_step(step_name)` (heartbeat) e um `dry_run` vindo do job;
   remover a etapa "Portal de Chamados" (fica no portal); `_add_to_offboarding_queue` passa a
   devolver `{ms_user_id, email, offboard_date}` no `resultado` em vez de gravar JSON local.
3. **`limpar-licencas`** vira o tipo de job `REVOGAR_LICENCA` (mesmo código de
   `handle_license_queue`, sem `questionary.confirm`).
4. **Logging**: handler de arquivo desligado quando `WORKER_MODE=1`; stdout JSON por linha com
   `job_id`/`chamado`; senhas mascaradas (já existe `mask_sensitive_str`).
5. **`config/settings.py`**: remover defaults reais do SIP; adicionar `PORTAL_API_URL`,
   `AUTOMACAO_WORKER_TOKEN`, `WORKER_ID`, `WORKER_POLL_S`, `SAP_PROXY_URL` (vazio = direto);
   apagar `SUPABASE_*`. `services/sap_api.py` usa `SAP_PROXY_URL` na `Session` (Seção 2.1).
6. **Dockerfile** (novo): base `mcr.microsoft.com/playwright/python` na mesma versão pinada
   do `requirements.txt` + binários do Tailscale (`tailscale`/`tailscaled`); `entrypoint.sh`
   sobe o `tailscaled` em userspace, faz `tailscale up` (falha = exit ≠ 0) e só então
   `exec python worker.py`. Usuário não-root. Mesma imagem serve a VM on-prem (plano B —
   lá, sem `TS_AUTHKEY`, o entrypoint pula o Tailscale).
7. **Testes**: `tests/test_worker.py` com a API do portal mockada e o orquestrador em dry-run —
   cobre mapeamento de payload, heartbeat, resultado e backoff. A suíte atual (`tests/`) é
   majoritariamente exploratória contra sistemas reais; não entra no CI.
8. O CLI interativo **continua existindo** (fallback manual e execuções fora de chamado).

**Como ficou (F3, 2026-09-15)** `[DECISÃO DE ENGENHARIA]`:
- `flow.py` foi reescrito em torno de um único `_run_step` (mesmos `step_name`
  da v1.0.0 — são contrato). Sem TTY, falha de etapa vira **`FAILED` e o fluxo
  segue** (antes virava `SKIPPED`, ambíguo com "pulada"). `SKIPPED` fica reservado
  para `pular_etapas` (mensagem fixa `já concluída em execução anterior`) e para
  o "pular" do operador na CLI.
- Novo `run_license_revoke_flow` (`REVOGAR_LICENCA`, etapa única) com
  `MSGraphService.remove_license` — o `limpar-licencas` da CLI antes só marcava
  o JSON, não removia nada no Graph.
- `models/factories.py` traduz o payload do contrato; gera **senha M365
  aleatória por job** (16 chars; antes a CLI usava `Bondmann@2026` fixo) e
  **senha SAP aleatória** (10 chars) — `senha_sap` entrou nas `credenciais` do
  contrato (aditivo, sem bump de versão).
- Heartbeat: um por etapa **e** um ticker em thread a cada `WORKER_HEARTBEAT_S`
  (120 s) durante etapas longas do Playwright; 409 em qualquer um aborta sem
  enviar resultado.
- `PortalChamadosService` e as envs `SUPABASE_*` **foram removidos** do projeto
  (conta do portal criada pelo próprio portal); `automation.log` e
  `offboarding_queue.json` só existem na CLI.
- Bug da F2 achado pelo e2e: o portal contava as etapas `SKIPPED — já concluída`
  como pendência, então uma reexecução nunca chegava a `CONCLUIDO`. Corrigido
  em `domain/automacao.py` (`etapa_concluida`).
- Verificação: `tests/test_worker.py` (22 testes, portal falso em HTTP real) e um
  e2e com o **app real do portal** (uvicorn, repositório em memória) + worker
  real em dry-run fechando um job de cada tipo (CRIACAO ×2, incl. reexecução;
  DESLIGAMENTO; REVOGAR_LICENCA). Imagem Docker escrita mas **não construída**
  (sem Docker na máquina) — primeiro build no Railway.

---

## Seção 7 — Segurança

- **Privilégio do worker:** ele só conhece `AUTOMACAO_WORKER_TOKEN` + credenciais dos sistemas
  externos. Zero acesso ao banco do portal. Token rotacionável por env sem deploy do worker
  (reinício basta).
- **Blast radius:** as credenciais de Graph app-only (criam/bloqueiam contas do tenant
  inteiro), do SAP e a `TS_AUTHKEY` ficam **só** nas env vars do serviço `automacao-worker`
  — nunca no serviço web do portal. A ACL do Tailscale limita o worker a `10.151.4.40:50000`;
  comprometer o container não dá acesso ao resto da rede.
- **Payload sem segredos, resultado mascarado** (Seção 5.1). Credenciais geradas só na mensagem
  de encerramento ao RH (D4), nunca no e-mail de notificação. Nada em `dados_formulario` além
  do que o RH digitou.
- **Gate humano + aprovação registrada** (`aprovado_por`/`aprovado_em`) + `historico_chamados`
  recebe uma entrada por transição de job (auditoria imutável já existente).
- **RLS:** `automacao_jobs` legível só pelo staff do setor de destino (TI) e ADMIN; escrita
  exclusivamente via `admin_connection()` nas rotas da API e do card.
- **Validação servidor-side** de tudo que o worker vai executar (e-mail no domínio, região no
  catálogo, perfil no enum) — o worker confia no payload porque o portal validou.
- **Higiene imediata (independe do projeto):** tirar o `.env` do zip de circulação; remover
  defaults hard-coded do CompanySIP em `settings.py`; conferir se as credenciais admin do
  WMW/SIP são contas dedicadas à automação (rastreabilidade nos logs desses sistemas).

---

## Seção 8 — Cronograma (F0–F5) com DoD

Esforço em dias úteis de uma pessoa; F1 e F3 podem andar em paralelo (repos diferentes).

| Fase | Entrega | DoD | Esforço |
|---|---|---|---|
| **F0 — Decisões e docs** | D1–D4 respondidas; ADR-0009 (topologia do worker: pull via HTTPS, execução fora do portal); Seção 5 do plano mestre com a DDL; `docs/automacao_api.md` v1 | Docs mergeados; gestor validou os campos das tabelas 4.2/4.3 com o RH | 1 |
| **F1 — Formulários estruturados** (portal) | Generalização do Químico (4.1), layouts 4.2/4.3, gate D3, `wmw_regions.json` versionado no portal, título/descrição automáticos, exibição rotulada | Testes: `test_formularios_dinamicos` (validação, condicional, gate) + fluxo em `test_portal`; Químico sem regressão (`test_formularios_quimico` verde); CSS buildado no CI (memória: `app.css` gitignored). **Entrega valor sozinha**: TI passa a receber pedidos completos mesmo antes do worker | 3–4 |
| **F2 — Fila, gate e API** (portal) | Migration `0091`, criação do job na abertura, card no atendimento (aprovar / dry-run / cancelar / reexecutar pendências), API 5.3, processamento do resultado (notas, e-mail, `REVOGAR_LICENCA`), vigilância 5.4, envs (`AUTOMACAO_*`) | Testes de rota com token, claim concorrente (2 workers, 1 job), transições, RLS (`tests/e2e -m rls`); `/health/ready` reporta worker | 4–5 |
| **F3a — Túnel** (infra Bondmann) | Subnet router Tailscale on-prem, rota aprovada, tag + ACL + auth key efêmera (Seção 2.1) | De um container de teste no Railway, `curl -x http://localhost:1055 https://10.151.4.40:50000/b1s/v1/Login` responde (401/405 já prova alcance) | 0,5–1 (infra) |
| **F3 — Worker** (automação) | Itens 1–7 da Seção 6; imagem Docker com Tailscale; serviço `automacao-worker` no Railway; runbook de operação (`docs/runbook_automacao_worker.md`) | `worker.py` em dry-run contra o portal local fecha um job de cada tipo ponta a ponta; no Railway, autentica no `/saude` e o `Login` do SAP via túnel devolve `SessionId` | 4–5 |
| **F4 — Homologação** | (1) dry-run real via card em produção; (2) criação real de **um usuário de teste** por perfil (3 jobs) e desligamento do mesmo; (3) `REVOGAR_LICENCA` forçado com `executar_apos` manual | Todas as etapas `SUCCESS` ou pendência explicada; notas internas legíveis pelo TI sem abrir log; alerta de heartbeat testado matando o worker no meio de um dry-run | 3 |
| **F5 — Rollout e evolução** | Liga para o RH; 2 semanas com gate; depois avaliar `AUTOMACAO_AUTOEXEC_TIPOS=CRIACAO`; autocomplete de colaborador no desligamento; migração do worker para Railway se o túnel existir (D1b) | KPIs da Seção 9 por 30 dias; ADR atualizado se a topologia mudar | 2 + contínuo |

Total até produção com gate: **~15–18 dias úteis** (3,5 semanas com uma pessoa; ~2,5 com F1‖F3).

---

## Seção 9 — Riscos, KPIs e protocolo de atualização

### 9.1 Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Worker para (crash, deploy quebrado) e ninguém percebe (precedente wuzapi) | jobs param na fila | Railway reinicia; vigilância 5.4 + e-mail ao TI; card mostra "aguardando worker há X" |
| Servidor subnet router on-prem cai / auth key expira | só as etapas SAP falham | fluxo segue com `SKIPPED`; `/saude` expõe estado do Tailscale; monitorar o servidor; auth key reusable sem expiração curta, rotação documentada no runbook |
| Scraper WMW/SIP quebra por mudança de tela | etapa `FAILED` | fluxo continua (SKIP), TI faz manual, reexecução só das pendências; manter modo `--headed` local para consertar seletor |
| Desligamento executado no chamado errado | bloqueio indevido de conta | gate humano (D2), resumo do payload no card antes do clique, dry-run disponível; reversão documentada no runbook (reativar conta, remover regra de encaminhamento) |
| Licença M365 atribuída automaticamente a todo novo usuário | custo | ⚠️ confirmar com o gestor que Business Basic é sempre correto; se não, vira campo `select` no formulário |
| SAP em PRD sem homologação documentada de criação de usuário interno | usuário/licença errados no ERP | F4 item (2) cobre; até lá, `licencas_sap` pode ser forçado a vazio por env |
| Drift entre os dois repos (contrato da API) | worker incompatível após deploy do portal | `docs/automacao_api.md` versionado; `/saude` devolve `versao_contrato`; worker recusa rodar se diferente |

### 9.2 KPIs (30 dias após F5)

- Tempo médio abertura → conta criada (meta: < 1 dia útil; hoje manual, sem medição).
- % de jobs `CONCLUIDO` sem pendência (meta: ≥ 80%); etapas que mais falham (por sistema).
- Zero incidentes de desligamento indevido; zero credenciais em e-mail de notificação ou em
  `resultado`/`payload` (checagem no red team existente, `tests/red_team/`).

### 9.3 Protocolo de atualização

Igual à Seção 7 do plano mestre: a cada PR desta frente, atualizar a tabela abaixo, o
`docs/CHANGELOG.md` e — se DDL/RLS/contrato mudar — a Seção 5 daqui **antes** do código.
Decisões D1–D4 respondidas viram texto fixo na Seção 0.2 (removendo a marca de pendente).

---

## Tabela de Estado de Implementação

| Feature | Status | Fase | Observações |
|---|---|---|---|
| Decisões D1–D4 | ✅ Todas decididas em 2026-09-14 | F0 | Seção 0.2 |
| ADR-0009 + DDL na Seção 5 do plano mestre + `docs/automacao_api.md` | ⏳ | F0 | — |
| Formulários dinâmicos por subcategoria (generalização do Químico) | ✅ Código completo (2026-09-14) | F1 | `campos_dinamicos.py` (motor + `visivel_se`), `formularios_dinamicos.py` (registro), Químico delega; partial `_campos_dinamicos.html`; cascade por subcategoria |
| Layouts Criação / Desligamento + gate D3 | ✅ Código completo (2026-09-14) | F1 | `formularios_acessos.py`; 117 regiões WMW; e-mail `@bondmann.com.br` + sugestão; 37 testes novos; validado no browser com repo fake. Pendente: deploy + conferir com o RH os rótulos das tabelas 4.2/4.3 |
| `automacao_jobs` + card no atendimento + API do worker + vigilância | ✅ Código completo (2026-09-14); migration `0091` aplicada em produção | F2 | `docs/automacao_api.md` v1; 36 testes; envs `AUTOMACAO_*` a configurar no Railway (`AUTOMACAO_ATIVA=false` até a F4). Pendente: e2e RLS da 0091 |
| Túnel Tailscale (subnet router on-prem + ACL + auth key) | ⏳ Depende da infra | F3a | pré-requisito do passo SAP |
| `worker.py` + Dockerfile (Playwright + Tailscale) + serviço Railway + runbook | ✅ Código completo (2026-09-15) | F3 | repo `Automação/` (agora com git local): `worker.py`, `models/factories.py`, `flow.py` (on_step/skip_steps/REVOGAR_LICENCA), `Dockerfile` + `entrypoint.sh` + `railway.json`, 22 testes; runbook em `docs/runbook_automacao_worker.md`. Pendente: build da imagem e serviço no Railway (depende de F3a para o passo SAP) |
| Homologação (dry-run + usuário de teste + revogação de licença) | ⏳ | F4 | — |
| Rollout com gate → avaliação de auto-execução da criação | ⏳ | F5 | — |

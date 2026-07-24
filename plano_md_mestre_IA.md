# Plano Mestre — Agentes de IA para Triagem de Chamados (TI e Químico)

> **Documento vivo.** Fonte de contexto persistente para todo o desenvolvimento dos agentes de
> IA de triagem do Portal de Chamados Bondmann. Deve ser revisado e atualizado ao final de cada
> PR, feature ou correção complexa desta frente (ver Seção 9 — Protocolo de Atualização).
>
> **Fonte de verdade do produto:** `Plano_Mestre_Agentes_IA_Triagem (1).docx` (v1.1, 22/07/2026).
> Este MD **consolida, corrige e detalha** a planificação para execução pelo Claude Code — não a
> substitui nem reinventa. Divergências entre o docx e a realidade do repositório estão resolvidas
> na **Seção 0.1** (obrigatória). Toda decisão de implementação não coberta pela planificação está
> marcada como `[DECISÃO DE ENGENHARIA]`; toda afirmação não verificável, como `⚠️ SUPOSIÇÃO A VALIDAR`.
>
> **Relação com o plano mestre geral:** este documento governa **apenas** a frente de IA de
> triagem. Convenções de stack, segurança, RLS, testes e deploy vêm do
> [`plano_mestre_desenvolvimento.md`](plano_mestre_desenvolvimento.md) e **prevalecem** onde este
> doc for omisso (em especial: Seção 3 — segurança/RLS, Seção 4 — testes, Seção 6.2 — regra dura
> de segredos). O código segue o doc, não o contrário.

---

## Sumário

- [Seção 0 — Regras de ouro para o Claude Code](#seção-0--regras-de-ouro-para-o-claude-code)
- [Seção 0.1 — Correções à planificação (obrigatório)](#seção-01--correções-à-planificação-obrigatório)
- [Seção 1 — Estado atual do repositório (ponto de partida real)](#seção-1--estado-atual-do-repositório-ponto-de-partida-real)
- [Seção 2 — Arquitetura do motor de triagem](#seção-2--arquitetura-do-motor-de-triagem)
- [Seção 3 — Segurança do Agente Químico (dois passes)](#seção-3--segurança-do-agente-químico-dois-passes)
- [Seção 4 — Modelo de dados (DDL canônico da frente de IA)](#seção-4--modelo-de-dados-ddl-canônico-da-frente-de-ia)
- [Seção 5 — Busca de chamados semelhantes](#seção-5--busca-de-chamados-semelhantes)
- [Seção 6 — Modelos, provedores e custos](#seção-6--modelos-provedores-e-custos)
- [Seção 7 — Cronograma modular (F0–F6) com DoD](#seção-7--cronograma-modular-f0f6-com-dod)
- [Seção 8 — Testes, QA e Red Team](#seção-8--testes-qa-e-red-team)
- [Seção 9 — Protocolo de Atualização de Contexto (doc vivo)](#seção-9--protocolo-de-atualização-de-contexto-doc-vivo)
- [Seção 10 — Riscos, KPIs e Governança](#seção-10--riscos-kpis-e-governança)
- [Changelog](#changelog)
- [Tabela de Estado de Implementação](#tabela-de-estado-de-implementação)

---

## Seção 0 — Regras de ouro para o Claude Code

Regras permanentes desta frente. Valem para **toda** sessão, fase e PR:

1. **Anti-alucinação:** nenhuma afirmação sobre o repositório sem verificar o arquivo real.
   Os caminhos citados neste doc existem (foram conferidos em 22/07/2026); se um caminho não
   bater, **pare e atualize este doc antes de codar**. Nunca inventar nome de tabela, coluna,
   função ou env var — a fonte é a Seção 4 (DDL) e a Seção 2.4 (config).
2. **Numeração de migrations:** sempre conferir `ls supabase/migrations/` antes de criar uma
   migration — a numeração avança a cada sessão (ver correção C1 na Seção 0.1). Migrations são
   **idempotentes** (padrão do projeto, ex.: `0049`) e imutáveis após merge.
3. **A IA nunca conclui atendimento**, nunca muda categoria/prioridade/status sozinha (apenas
   sugere na nota interna), e nunca dialoga além do ciclo de triagem (máx. **2 rodadas** de
   perguntas por chamado — limite materializado no banco, Seção 4.1).
4. **Invariante inegociável (Químico):** o passe que gera texto visível ao usuário (Passe A)
   **não recebe dados sigilosos no contexto**; a saída do Passe B **só** é gravada como nota
   interna (`is_interna = true` fixado em código). Quebra desta invariante = build vermelho
   (teste automatizado, Seção 8.2). As **quantidades das formulações não entram em contexto de
   modelo algum** — nem no Passe B.
5. **Falha silenciosa:** qualquer erro de IA (timeout, quota, JSON inválido) é logado e engolido;
   o chamado segue o fluxo normal. A triagem **nunca** bloqueia nem atrasa a abertura do chamado
   (mesmo padrão já usado em `app/services/ia_resumo.py`).
6. **Segredos:** aplica-se integralmente a "REGRA DURA" da Seção 6.2 do plano mestre geral —
   chave de API só em env var, nunca em código, doc, log ou commit.
7. **TDD adaptado:** nenhum módulo desta frente é "pronto" sem suíte pytest verde (unit para
   lógica pura; integração com RLS real para persistência; red team para o Químico).
8. **Prompt = código:** prompts versionados em `app/ia/prompts/` (arquivos, não strings inline).
   Toda alteração de prompt ou de modelo do agente Químico **reexecuta a bateria de red team**
   antes do merge (critério de permanência em produção).
9. **Custo auditável:** toda chamada de modelo registra tokens e custo estimado em `ia_triagens`.
   Nunca fazer loop de agente — **uma chamada com saída estruturada por passe**, custo previsível.
10. **Ao final de cada entrega:** atualizar este doc (Tabela de Estado + `docs/CHANGELOG.md`),
    conforme Seção 9.

---

## Seção 0.1 — Correções à planificação (obrigatório)

A planificação (docx v1.1) foi escrita sem visibilidade do estado atual do repositório. Cada
divergência tem **uma única decisão cravada**:

### C1 — Numeração de migrations

- **Docx diz:** "Tabela `ia_triagens`: `supabase/migrations/0030` (novo)".
- **Realidade:** `0030_categoria_outros.sql` **já existe**; a última migration é
  `0049_departamento_quimico.sql`.
- **DECISÃO:** a numeração desta frente começa em **`0050`** e segue a sequência livre no momento
  de cada fase (conferir `ls supabase/migrations/` sempre). Referências do docx a "0030" leem-se
  "migration da F0".

### C2 — Já existe IA em produção (`ia_resumo.py`) — absorver, não duplicar

- **Docx assume:** greenfield ("SDK instalado e esqueleto do módulo `app/ia/`" na F0).
- **Realidade:** `app/services/ia_resumo.py` (2026-07-22) já gera um **resumo por IA** na abertura
  de chamados do Químico → coluna `chamados.resumo_ia` (migration `0049`), via provedor
  compatível-OpenAI (OpenAI GPT-5.4 mini desde a decisão C5; antes Groq), disparado como `BackgroundTask` em
  `app/routes/portal.py::criar_chamado`, gravado por `app/db.py::admin_connection()`.
- **DECISÃO (rota de absorção):**
  1. **F0–F3:** `ia_resumo.py` continua operando intocado para o Químico. O motor de triagem
     nasce em `app/ia/` **reaproveitando** o padrão dele (httpx + API compatível OpenAI,
     tolerância a falha, background task) — o cliente HTTP é extraído para `app/ia/cliente.py`
     e `ia_resumo.py` passa a consumi-lo (refactor sem mudança de comportamento, coberto por
     `tests/test_ia_resumo.py`).
  2. **F4:** o Passe B do agente Químico **substitui** o resumo simples (a pré-análise técnica
     contém o resumo e mais). `gerar_e_salvar_resumo` é aposentado; a coluna `resumo_ia` é
     **mantida por compatibilidade** de template (`workspace/atendimento.html`) até a UI ler a
     nota interna da triagem, e então marcada como deprecada (nunca dropada em migration desta
     frente — sem impacto destrutivo).
- **Racional:** evita dois pipelines de IA concorrentes no mesmo evento de abertura.

### C3 — Saída da triagem: nota interna em `mensagens`, não coluna

- **Contexto:** o resumo atual vive numa **coluna** (`resumo_ia`); o docx pede notas assinadas
  por um perfil "Assistente IA" na **conversa** do chamado.
- **DECISÃO:** a pré-análise e as perguntas da triagem são **mensagens** (`mensagens` com
  `remetente_id` = perfil "Assistente IA"; nota = `is_interna=true`, pergunta = `is_interna=false`)
  — auditáveis, visíveis no chat, entregues pelo Realtime existente e cobertas pela RLS vigente
  (o autor nunca vê nota interna — política já validada e2e). A coluna `resumo_ia` não recebe
  novos usos.

### C4 — Perfil "Assistente IA" exige usuário de serviço no Supabase Auth

- **Contexto:** `perfis.id` tem FK para `auth.users(id)` — não dá para inserir um perfil "solto".
- **DECISÃO DE ENGENHARIA:** criar via Admin API um usuário de serviço
  (e-mail interno, ex.: `assistente-ia@bondmann.internal`, senha aleatória descartada, login
  jamais usado), promovido por SQL a perfil `nome='Assistente IA'`, `role='OPERADOR'`,
  `departamento_id=NULL`. Documentado em `supabase/registro_usuarios.sql`. O app **nunca**
  autentica com esse usuário: as escritas da IA usam `admin_connection()` (escrita de sistema,
  padrão já existente), com `remetente_id` apontando para esse perfil.
  `⚠️ SUPOSIÇÃO A VALIDAR (F0):` policies de INSERT em `mensagens` não bloqueiam a conexão
  administrativa (hoje não bloqueiam — `_salvar_resumo` já escreve assim).

### C5 — Provedor de IA definido: OpenAI GPT-5.4 mini (Groq descontinuado)

- **Docx recomenda:** Haiku 4.5 ou GPT-5.4 mini, critério de desempate = billing já existente;
  DPA com não-treinamento obrigatório para o Químico.
- **`[DECISÃO DO GESTOR 2026-07-22]`:** provedor = **OpenAI**, modelo = **`gpt-5.4-mini`**, para
  todos os agentes (TI e Químico, Passes A e B). O **Groq é descontinuado** — inclusive no
  `ia_resumo.py` atual, que já é compatível-OpenAI: a troca imediata é **só env** (apontar
  `GROQ_BASE_URL=https://api.openai.com/v1`, `GROQ_MODEL=gpt-5.4-mini` e `GROQ_API_KEY` para a
  chave OpenAI no Railway/`.env`), sem mudança de código. Na **F0**, as settings `groq_*` são
  **renomeadas** para nomes provedor-neutros (as `IA_TRIAGEM_*` da Seção 2.4), mantendo a camada
  compatível-OpenAI isolada em `app/ia/cliente.py` (troca futura de provedor continua sendo só env).
- **Gate do Químico mantido:** a API da OpenAI não treina com dados enviados por padrão, mas o
  **DPA formal** (assinável no dashboard da OpenAI) precisa estar aceito pela conta da empresa
  antes do go-live do Químico — F4. `[AÇÃO DO GESTOR PENDENTE]:` aceitar o DPA na conta OpenAI e
  `⚠️ VALIDAR` a política de retenção (Zero Data Retention é opcional/por contrato).
- **🔒 Segurança da chave:** a chave OpenAI foi transmitida via chat em 2026-07-22 — pela regra
  dura da Seção 6.2 do plano mestre geral, chave exposta em chat é tratada como **comprometida**:
  rotacioná-la no dashboard da OpenAI e cadastrar a nova **somente** como env var (Railway /
  `.env` local no `.gitignore`). Nunca neste doc, em código ou em commit.

### C6 — Timeout unificado

- Docx: 30 s. `ia_resumo.py` atual: 20 s. **DECISÃO:** **30 s por passe**, configurável
  (`IA_TRIAGEM_TIMEOUT_S`). `ia_resumo.py` herda o valor ao migrar para `app/ia/cliente.py`.

### C7 — "Fora do alcance do worker" vira garantia de banco, não promessa de código

- **Docx diz:** quantidades das formulações "fora do alcance do worker de triagem".
- **DECISÃO DE ENGENHARIA (endurece o docx):** as quantidades ficam em tabela própria
  (`base_quimico_formulacoes`) e a leitura da base sigilosa pelo Passe B usa uma **conexão
  dedicada com role Postgres `ia_worker`** que possui GRANT de SELECT nas tabelas
  `base_quimico_*` **exceto** `base_quimico_formulacoes` (sem GRANT = erro de permissão, não
  disciplina de código). O `admin_connection()` continua existindo para as **escritas** de
  mensagem/auditoria, mas o **contexto do modelo** só é montado a partir da conexão `ia_worker`.
  Teste de integração prova que `SELECT` em `base_quimico_formulacoes` com o role `ia_worker`
  falha (Seção 8.2).

---

## Seção 1 — Estado atual do repositório (ponto de partida real)

Verificado em 2026-07-22. O que **já existe** e será reutilizado:

| Peça existente | Onde | Papel na frente de IA |
|---|---|---|
| Resumo por IA na abertura do Químico (compatível-OpenAI; GPT-5.4 mini desde C5) | `app/services/ia_resumo.py` + hook em `app/routes/portal.py::criar_chamado` (BackgroundTask do Starlette) | Embrião do motor; absorvido conforme C2. O hook é o **mesmo ponto de disparo** da triagem. |
| Departamento Químico com fila + formulários dinâmicos | migration `0049`, `app/domain/formularios_quimico.py`, `chamados.dados_formulario` (jsonb) | Os campos estruturados do formulário são **input de alta qualidade** para a triagem (menos perguntas necessárias). |
| Notas internas | `mensagens.is_interna` + RLS validada e2e (autor nunca vê) | Canal de saída do Passe B / pré-análise. |
| Conversa + Realtime + fallback polling | `app/repositories/mensagens.py::adicionar_mensagem`/`responder_staff`, `chat.js` | Perguntas da IA aparecem no chat como mensagem pública normal. |
| E-mail transacional + resposta inbound | `app/notification.py::agendar_notificacao_email` (Reply-To tokenizado), rotas inbound em `app/routes/common.py` | Notifica o usuário das perguntas da IA; a resposta (portal **ou** e-mail) dispara a re-triagem. |
| Escrita de sistema sem claims | `app/db.py::admin_connection()` | Gravação de mensagens da IA e de `ia_triagens`. |
| Config por env (Pydantic Settings) | `app/config.py` (`groq_api_key`, `groq_model`, `groq_base_url`, property `ia_resumo_ativo`) | Padrão a seguir para as flags de triagem (Seção 2.4). Hoje apontadas para a OpenAI (C5); renomeadas para `IA_TRIAGEM_*` na F0. |
| Auditoria de chamado | `historico_chamados` | Registrar evento `IA_TRIAGEM` por rodada. |
| Suíte de testes + e2e RLS | `tests/test_ia_resumo.py`, `tests/test_formularios_quimico.py`, `tests/e2e/` (marker `rls`) | Base dos testes desta frente; red team entra como marker novo. |

O que **não existe** e será criado: `app/ia/` (motor, prompts, cliente), tabelas `ia_triagens` e
`base_quimico_*`, perfil "Assistente IA", índice FTS português, script de ingestão da base do
Químico, bateria de red team.

---

## Seção 2 — Arquitetura do motor de triagem

### 2.1 Decisões de arquitetura (da planificação, confirmadas)

- **Integração nativa:** o agente roda dentro do serviço FastAPI (Railway), disparado como
  background task após o INSERT do chamado. **Sem n8n/orquestrador externo** — menos custo,
  menos superfície de falha, reuso das regras de negócio.
- **Uma chamada com saída estruturada por passe** (JSON validado por schema Pydantic). Sem loop
  de agente. Retry único em JSON inválido; depois, falha silenciosa registrada em `ia_triagens`
  com `acao='ERRO'`.
- **Os dois agentes compartilham o mesmo motor**; a diferença é o contexto carregado (prompt +
  fontes) e as salvaguardas (Químico = dois passes, Seção 3).
- **Kill switch e flags por env** — desativação imediata sem deploy.

### 2.2 Componentes (mapa de arquivos)

| Componente | Responsabilidade | Localização |
|---|---|---|
| Gancho de disparo | Agenda a triagem em background após criar o chamado (junto do e-mail). | `app/routes/portal.py::criar_chamado` (estender o hook existente da `tarefa_ia`) |
| Gancho de re-triagem | Ao receber resposta do **autor** (portal ou inbound e-mail) num chamado em triagem, reagenda a triagem com a conversa completa. | `app/routes/portal.py` (mensagem do autor) + `app/routes/common.py` (inbound) |
| Motor de triagem | Monta contexto, chama o modelo com saída estruturada, decide ação (nota / perguntas / nada), grava auditoria. | `app/ia/triagem.py` (novo) |
| Cliente de modelo | Chamada HTTP compatível-OpenAI: timeout, retry de JSON, contagem de tokens, custo. ~30 linhas, provedor-agnóstico. | `app/ia/cliente.py` (novo; extraído de `ia_resumo.py`, ver C2) |
| Prompts | Prompt mestre TI; prompts Passe A / Passe B do Químico; roteiros de perguntas. Arquivos versionados. | `app/ia/prompts/` (novo) |
| Schemas de saída | Modelos Pydantic do JSON de cada passe (suficiência, categoria sugerida, perguntas[], pré-análise, semelhantes[]). | `app/ia/schemas.py` (novo) |
| Contexto do Químico | Recuperação seletiva na base `base_quimico_*` via conexão `ia_worker` (produto citado → linhas + ficha). | `app/ia/contexto_quimico.py` (novo) |
| Busca de semelhantes | FTS português sobre chamados resolvidos do mesmo departamento (Fase B: pgvector). | `app/repositories/ia_busca.py` (novo) |
| Escrita de notas/perguntas | Mensagens assinadas pelo perfil "Assistente IA" (nota=interna, pergunta=pública) + `historico_chamados` + e-mail. | reuso: `admin_connection()`, `app/notification.py` |
| Auditoria de IA | Tabela `ia_triagens` (Seção 4.1). | migration `0050` (F0) |
| Ingestão da base Químico | Script re-executável: planilha (16 abas) + PDF de fichas → tabelas `base_quimico_*`. | `scripts/ingestao_base_quimico.py` (novo, F4) |
| Configuração | Flags/modelo/limites/timeout por env. | `app/config.py` + Railway |

### 2.3 Fluxo de triagem (máquina de estados)

```
Chamado criado (depto com IA ativa)
        │  background task (nunca bloqueia o redirect)
        ▼
[Rodada N] Verificação: categoria correta? informações suficientes?
        │
        ├── Suficiente ──────────────► NOTA INTERNA direto (pré-análise + semelhantes
        │                              + sugestão de categoria/prioridade se divergente)
        │                              → fim da triagem
        │
        ├── Insuficiente e N < 2 ────► PERGUNTAS ao usuário (mensagem pública
        │                              + e-mail com Reply-To inbound) — SEM nota
        │                              nesta rodada: o ciclo de perguntas vem primeiro
        │                              → aguarda resposta
        │                                   │ autor responde (portal OU e-mail)
        │                                   ▼
        │                              re-triagem com a conversa completa (Rodada N+1)
        │                              → NOTA INTERNA fecha o ciclo
        │
        └── Insuficiente e N = 2 ────► NOTA INTERNA com o que há, sinalizando
                                       explicitamente as lacunas → fim
```

Regras duras do fluxo:

- **Ordem nota × perguntas (decisão do usuário, 2026-07-23):** com informação SUFICIENTE a nota
  interna sai DIRETO na rodada 1; com informação INSUFICIENTE o ciclo de perguntas ao autor vem
  primeiro (até 2 rodadas) e a **nota interna fecha o ciclo** — na rodada seguinte à resposta do
  autor, ou no teto de rodadas com as lacunas sinalizadas. (Houve no mesmo dia uma variante
  "nota sempre, junto da pergunta", revertida a pedido do usuário.)
- **A nota da rodada 1 com atendimento já iniciado continua saindo**: o atendente assumir o
  chamado segundos após a abertura NÃO suprime a pré-análise — com atendimento iniciado
  (`status ≠ 'NOVO'` ou `operador_id` preenchido), a ação é **forçada a `NOTA_INTERNA`** (nunca
  `PERGUNTAS`). Só chamado `RESOLVIDO` não é triado. A guarda é reavaliada **após** a chamada de
  modelo (estado fresco na persistência): se o atendente assumiu durante a espera, a pergunta
  pública vira nota interna.
- **Re-triagem (rodada > 1) só enquanto nenhum atendente atua**: condição `status = 'NOVO'` **e**
  `operador_id IS NULL` verificada no momento de executar a rodada. (Interpelar o autor com o
  caso já em atendimento humano seria ruído — a regra original do docx vale só para perguntas.)
- **Máx. 2 rodadas de perguntas** — materializado por `UNIQUE(chamado_id, rodada, passe)` em
  `ia_triagens` (idempotência com retries inclusa).
- **Sem resposta do usuário:** não há job agendado de expiração na v1 — a nota "com lacunas" é
  gerada quando um atendente assume (o contexto disponível já está nas triagens anteriores) ou
  na rodada 2. `[DECISÃO DE ENGENHARIA]` evita criar scheduler novo; reavaliar na F6 se os
  atendentes sentirem falta.
- **Modo sombra** (`IA_TRIAGEM_MODO_SOMBRA=true`): o motor roda tudo, mas **só grava notas
  internas** — nunca mensagens públicas nem e-mail. É o modo de F1. **A saída da sombra é POR
  DEPARTAMENTO** (F2, decisão do usuário 2026-07-24 — sombra validada: notas úteis e perguntas
  pertinentes): `IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS` (CSV) lista os departamentos liberados
  para perguntas públicas **mesmo com a sombra global ligada** — permite o TI sair da sombra
  (F2) mantendo o Químico em sombra até o red team (F5/F6, gate de zero vazamentos).
  `IA_TRIAGEM_MODO_SOMBRA=false` continua significando "ninguém em sombra" (estado final F6);
  a lista é irrelevante nesse caso.

### 2.4 Configuração (env vars — nomes canônicos)

Seguem o padrão do `app/config.py` (Pydantic Settings, defaults seguros = desligado):

| Env var | Default | Efeito |
|---|---|---|
| `IA_TRIAGEM_ATIVA` | `false` | **Kill switch geral.** `false` = nenhum agente roda (nem sombra). |
| `IA_TRIAGEM_DEPARTAMENTOS` | `""` | CSV de nomes de departamentos com triagem (ex.: `TI` ou `TI,Dpto Químico`). Vazio = nenhum. |
| `IA_TRIAGEM_MODO_SOMBRA` | `true` | Só notas internas; sem perguntas públicas/e-mail. `false` = ninguém em sombra (F6). |
| `IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS` | `""` | CSV de departamentos liberados para perguntas públicas **com a sombra global ligada** (ex.: `TI`) — saída da sombra por departamento (F2). Vazio = ninguém sai; irrelevante com `IA_TRIAGEM_MODO_SOMBRA=false`. |
| `IA_TRIAGEM_MODEL` | `gpt-5.4-mini` | Modelo do Passe A / agente TI (C5). |
| `IA_TRIAGEM_MODEL_PASSE_B` | `""` | Opcional; vazio = usa `IA_TRIAGEM_MODEL`. |
| `IA_TRIAGEM_BASE_URL` | `https://api.openai.com/v1` | Endpoint compatível-OpenAI (`/chat/completions`). Trocar de provedor = trocar esta URL + modelo. |
| `IA_TRIAGEM_API_KEY` | `""` | Chave do provedor (hoje: chave OpenAI). Vazio = triagem desligada (mesmo padrão do `ia_resumo_ativo`). **Só via env** — nunca em código/doc (C5). |
| `IA_TRIAGEM_TIMEOUT_S` | `30` | Timeout por passe (C6). |
| `IA_TRIAGEM_MAX_RODADAS` | `2` | Teto de rodadas de perguntas. |
| `IA_WORKER_DATABASE_URL` | `""` | Conexão do role `ia_worker` (C7). Obrigatória só para o Químico (F4). |

`[DECISÃO DE ENGENHARIA]` As flags de F0 entram **todas de uma vez** (com defaults desligados),
para que ligar/desligar fases seguintes seja só env no Railway, sem deploy.

---

## Seção 3 — Segurança do Agente Químico (dois passes)

### 3.1 Princípio: proteção estrutural, não promessa de prompt

Instruir o modelo a "não vazar" é insuficiente (prompt injection via descrição do chamado). A
proteção é estrutural: **não é possível vazar o que não está no contexto.**

- **Passe A (canal público):** recebe **apenas** os dados do próprio chamado (incl.
  `dados_formulario`) e o **playbook de perguntas de diagnóstico** (roteiros por sintoma, sem
  qualquer dado de produto/formulação). Verifica categoria e suficiência; redige as perguntas.
  É o **único** passe cuja saída pode virar mensagem pública.
- **Passe B (canal interno):** recebe o chamado + base sigilosa (recuperação seletiva, Seção 3.3).
  Produz a pré-análise técnica (metodologia 6M) e as referências de casos semelhantes. Sua saída
  é gravada **exclusivamente** como nota interna — `is_interna=True` é **argumento fixo no código**
  da função de persistência do Passe B (não um parâmetro), coberto por teste (Seção 8.2).
- **Quantidades das formulações:** permanecem só no banco (`base_quimico_formulacoes`), sem GRANT
  ao role `ia_worker` (C7). Não entram em contexto de modelo algum.

O agente **TI** usa passe único (equivalente ao Passe A + nota interna): não há base sigilosa,
então não há canal a segregar — mas a mesma invariante "pergunta pública só sai do fluxo de
perguntas" se aplica por construção (mesmo motor).

### 3.2 Camadas de proteção (ameaça → mitigação)

| Ameaça | Mitigação |
|---|---|
| Prompt injection na descrição para extrair formulações | Passe A não tem dados sigilosos no contexto; Passe B não escreve em canal público por construção. |
| Erro futuro rotear saída do Passe B para mensagem pública | `is_interna=True` fixado em código; teste de invariante falha o build (Seção 8.2). |
| Acesso indevido à base por outros caminhos do portal | Tabelas `base_quimico_*` sem policy RLS pública (RLS habilitado, zero policies = ninguém via PostgREST/claims); nenhuma rota da API expõe o conteúdo; leitura só pela conexão `ia_worker`. |
| Leitura das quantidades pelo worker | Sem GRANT de SELECT em `base_quimico_formulacoes` para `ia_worker` — erro de permissão no banco (C7). |
| Vazamento via logs/monitoramento | Saída do Passe B **não** vai a log em texto claro; logs guardam metadados (modelo, tokens, custo, duração, ação). Vale também para Sentry (não anexar payloads de IA a eventos). |
| Retenção pelo provedor de IA | Mínimo necessário por chamado; provedor com DPA/não-treinamento obrigatório para o Químico (C5, gate da F4). |
| Regressão após ajuste de prompt/modelo | Red team reexecutado a cada alteração (Seção 8.3) — critério de permanência em produção. |

### 3.3 Base de conhecimento: fontes existentes e destino por passe

A base já existe (hoje alimenta um assistente GPT no ChatGPT — arranjo **menos** seguro, pois a
planilha completa com quantidades fica anexada e a proteção é só o prompt): planilha de 16 abas
(61 produtos, 438 linhas de componentes com quantidades, 95 matérias-primas, playbooks, regras
de sigilo linha a linha), PDF de fichas técnicas (~50 produtos, 100% texto) e o prompt do
assistente (Word, ~80% reaproveitável). Mapeamento:

| Fonte / aba | Conteúdo | Destino |
|---|---|---|
| `Perguntas_Investigacao` + `Diagnostico_Ocorrencias` (colunas de coleta) | Playbooks de perguntas por sintoma — sem formulação. | **Passe A** (qualidade de perguntas sem dado sigiloso). |
| `Base_IA_Produtos` | Catálogo: aplicação, família técnica, componentes **sem** proporção, palavras-chave, orientação de resposta. | **Passe B**, recuperação seletiva por produto. |
| Fichas Técnicas (PDF) | Uso, diluição, restrições, parâmetros — fatiadas por produto (Chave Produto). | **Passe B**, só a ficha do(s) produto(s) citado(s). |
| `Diagnostico_Ocorrencias` (completa) + `Regras_Sigilo_Resposta` | Causas prováveis, escalonamento, regras por nível de sigilo. | **Passe B** (prompt/conduta da pré-análise). |
| `Compatibilidade_Materiais`, `Parametros_Controle`, `RCA_6M` | Abas-modelo em preenchimento pelo setor. | **Passe B** quando populadas — **não bloqueiam a v1**. |
| `Base_IA_Componentes` (quantidades) | A formulação de fato ("Confidencial / formulação"). | **NENHUM passe** — só no banco, sem GRANT ao worker (C7). |
| Prompt do GPT atual (Word) | Metodologia 6M, higiene epistêmica ("não invente", "hipótese provável"), estilo, limites. | Versão reduzida no Passe A; completa no Passe B. |

**Recuperação seletiva:** o motor identifica o produto citado (Chave Produto + palavras-chave que
a planilha já traz — e o campo "Produto" do formulário dinâmico do Químico, que é um select de 66
opções, dá o match exato de graça) e injeta só as linhas + ficha daquele produto (~3–8 mil
tokens), nunca a base inteira.

**Ingestão re-executável** (`scripts/ingestao_base_quimico.py`): lê planilha + PDF e faz upsert
nas `base_quimico_*` a cada nova versão dos arquivos (a base é viva — fichas atualizadas até
03/2026). Roda localmente pelo dev/gestor; registra `atualizado_em` por produto. Nunca é
executado a partir de rota HTTP.

---

## Seção 4 — Modelo de dados (DDL canônico da frente de IA)

> Contrato de modelagem; as migrations reais materializam este DDL. Nada de schema implícito.
> Nenhuma alteração nas tabelas existentes além das listadas.

### 4.1 `ia_triagens` — auditoria e idempotência (migration `0050`, F0)

```sql
CREATE TABLE ia_triagens (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id     uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  rodada         smallint NOT NULL CHECK (rodada >= 1),
  passe          text NOT NULL CHECK (passe IN ('UNICO', 'A', 'B')),
  acao           text NOT NULL CHECK (acao IN ('NOTA_INTERNA', 'PERGUNTAS', 'SEM_ACAO', 'ERRO')),
  resultado      jsonb NOT NULL DEFAULT '{}'::jsonb,   -- saída estruturada validada (sem dados sigilosos no caso do Passe B: só metadados + ids)
  modelo         text NOT NULL,
  tokens_entrada integer,
  tokens_saida   integer,
  custo_usd      numeric(10, 6),
  duracao_ms     integer,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (chamado_id, rodada, passe)                    -- idempotência sob retry + teto de rodadas
);
CREATE INDEX idx_ia_triagens_chamado ON ia_triagens(chamado_id);
-- RLS: habilitado; SELECT só para staff (mesmo escopo por departamento do chamado);
-- INSERT/UPDATE apenas pela conexão administrativa (nenhuma policy de escrita).
```

> `[DECISÃO DE ENGENHARIA]` `resultado` do **Passe B** guarda a estrutura da análise (ids de
> chamados semelhantes, produto identificado, flags) mas **não** o texto integral com dados de
> produto — o texto vive na `mensagens` interna (já protegida por RLS validada). Evita criar um
> segundo lugar sensível a proteger.

### 4.2 Perfil "Assistente IA" (F0 — ver C4)

Usuário de serviço no Supabase Auth + `UPDATE perfis SET nome='Assistente IA', role='OPERADOR'`.
O UUID entra em env/config (`IA_TRIAGEM_PERFIL_ID`) ou é resolvido por lookup de nome na
inicialização — `[DECISÃO DE ENGENHARIA]` lookup por nome com cache em memória (sem env extra,
sem hardcode de UUID).

### 4.3 Tabelas `base_quimico_*` (migration `0054_base_quimico.sql`, F4 — executada 2026-07-23)

> Schema CONFERIDO contra a planilha real em 2026-07-23 ("as abas mandam"):
> `Base_IA_Produtos` (61 linhas), `Base_IA_Componentes` (437), `Base_IA_Materias_Primas`
> (94), `Diagnostico_Ocorrencias` (5), `Perguntas_Investigacao` (6),
> `Regras_Sigilo_Resposta` (5). PDF de fichas: 71 páginas, 100% texto,
> ~1 ficha/página. Diferenças vs. o DDL planejado originalmente:
> `materias_primas` ganhou `codigo_mp` como PK (coluna real "Código MP") e
> `formula_quimica`/`utilizacoes`; `playbooks` unifica as 3 abas pequenas com
> coluna `tipo` (`DIAGNOSTICO` | `PERGUNTA_INVESTIGACAO` | `REGRA_SIGILO`) e
> `dados` jsonb (colunas da aba rotuladas); `formulacoes` ganhou `ordem` +
> `codigo_mp` + `UNIQUE(chave_produto, ordem)` (chave natural do upsert).

```sql
-- DDL completo em supabase/migrations/0054_base_quimico.sql. Resumo:
base_quimico_produtos        (chave_produto PK, segmento, codigo_produto, nome,
                              nome_normalizado, aplicacao, familia_tecnica, tipo_uso,
                              componentes jsonb,  -- nomes, SEM quantidades
                              palavras_chave text[], orientacao, atualizado_em)
base_quimico_materias_primas (codigo_mp PK, nome, formula_quimica, utilizacoes, atualizado_em)
base_quimico_playbooks       (id PK, tipo CHECK, sintoma, dados jsonb, atualizado_em,
                              UNIQUE (tipo, sintoma))
base_quimico_fichas          (chave_produto PK→produtos, conteudo, atualizado_em)
base_quimico_formulacoes     (id PK, chave_produto→produtos, ordem, codigo_mp,
                              componente, quantidade, funcao, atualizado_em,
                              UNIQUE (chave_produto, ordem))
```

- **RLS habilitado nas 5 tabelas; ZERO policies para papéis de usuário**
  (anon/authenticated ⇒ 0 linhas, mesmo com o GRANT default do Supabase).
- **Correção ao plano original (2026-07-23):** RLS também se aplica ao role
  `ia_worker` (só owner/superuser passam por cima) — logo as 4 tabelas
  liberadas têm, além do GRANT, uma **policy `FOR SELECT TO ia_worker USING (true)`**
  (`bq_*_ia_worker`). Isso não enfraquece nada: a policy é restrita ao role.
- `base_quimico_formulacoes`: **sem GRANT e sem policy** para `ia_worker`
  (`REVOKE ALL` explícito) ⇒ `SELECT` falha com erro de permissão —
  e2e `tests/e2e/test_rls_base_quimico.py` (marker `rls`) prova os dois lados.
- Role criado idempotente na migration com `LOGIN` **sem senha** (login
  impossível); a senha é definida pelo gestor fora da migration
  (`ALTER ROLE ia_worker PASSWORD ...` no SQL editor) e entra em
  `IA_WORKER_DATABASE_URL` — ver runbook.

### 4.4 Índice FTS em português (migration `0053`, F3 — aplicada em produção 2026-07-23)

> Numeração conferida (C1): `0052` foi tomada por outra frente
> (`0052_avatares_admin_marketing.sql`) no mesmo dia — a migration da F3 é a **`0053_chamados_fts.sql`**.

```sql
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS fts tsvector
  GENERATED ALWAYS AS (to_tsvector('portuguese', coalesce(titulo,'') || ' ' || coalesce(descricao,''))) STORED;
CREATE INDEX IF NOT EXISTS idx_chamados_fts ON chamados USING gin(fts);
```

A "resolução registrada" (o chamado não tem coluna de resolução) é obtida no momento da busca:
últimas mensagens **públicas de staff** do chamado resolvido (`mensagens` já indexada por
`chamado_id, created_at`). Essa resolução **também participa do casamento FTS**, concatenada ao
`c.fts` em tempo de busca (Seção 5) — a coluna gerada indexa só `titulo + descricao`; a resolução
entra via `to_tsvector` na query. `[DECISÃO DE ENGENHARIA]` — evita duplicar conteúdo em coluna
nova; se o volume tornar isso lento, promover a resolução a coluna materializada indexada em
migration futura.

### 4.5 Fase B — pgvector (migration futura, só quando o volume justificar)

Extensão `pgvector` (nativa no Supabase) + coluna `embedding` em `chamados`, gerada na resolução
e retroativamente (backfill). **Fora do escopo v1** — registrado para não se perder.

---

## Seção 5 — Busca de chamados semelhantes

Objetivo: a nota interna cita "o chamado X teve problema parecido; a solução registrada foi Y".

- **Fase A (F3, custo zero):** FTS português (Seção 4.4) sobre chamados `RESOLVIDO`/`FECHADO`
  **do mesmo departamento** (mesmo escopo de RLS do staff — a consulta roda na conexão
  administrativa, mas o filtro por `departamento_id` é obrigatório no SQL: defesa em
  profundidade, mesmo padrão do plano mestre geral). O motor extrai termos-chave do chamado novo
  (o próprio modelo devolve `termos_busca[]` no JSON do passe) e recebe os 3 melhores resultados
  (código + resolução).
- **Campos pesquisados (decisão do usuário, 2026-07-23):** o casamento e o ranqueamento cobrem
  **título + descrição** (coluna gerada `chamados.fts`) **e a resolução registrada** — a última
  mensagem pública de staff, concatenada ao `tsvector` em tempo de busca
  (`c.fts || to_tsvector('portuguese', coalesce(res.conteudo,''))`), sem coluna materializada
  nova. Assim um chamado é encontrado tanto pelo sintoma quanto pela **solução aplicada** (ex.:
  achar casos pela conduta "reinstalação do certificado A1", ainda que o título não a cite).
  Pesquisa e exibição usam o **mesmo texto** de resolução — nunca se cita uma resolução que não
  casou. `[DECISÃO DE ENGENHARIA]` o predicado passa a incidir sobre uma expressão, então o índice
  GIN `idx_chamados_fts` não filtra sozinho; os filtros duros (`departamento_id` +
  `status='RESOLVIDO'`) seguram o conjunto candidato na v1 — promover a resolução a coluna
  materializada indexada só se o volume justificar (mesma linha da Seção 4.4).
- **Fase B (futuro):** pgvector/embeddings — captura semelhança semântica ("tela azul" ≈
  "computador reinicia sozinho"). Gatilho: quando a Fase A começar a errar por vocabulário, com
  volume que justifique (~fração de centavo por chamado de custo de embedding).

Exemplo de nota gerada (alvo de qualidade):

> "Pré-análise: usuário relata erro ao emitir NF-e após atualização do certificado digital.
> Provável certificado A1 não instalado no novo computador. Casos semelhantes: BOND-2026-00412
> (resolução: reinstalação do certificado A1 e reconfiguração do emissor) e BOND-2026-00287
> (resolução: atualização da cadeia de certificação). Sugestão: validar com o usuário qual
> computador está em uso antes de escalar."

---

## Seção 6 — Modelos, provedores e custos

Estimativa por triagem: ~2.000 tokens de entrada / ~500 de saída por passe; Químico ≈ 2× (dois
passes). Preços de tabela (julho/2026, US$/M tokens):

| Modelo | Entrada | Saída | Custo/chamado | 1.000 chamados/mês |
|---|---|---|---|---|
| **GPT-5.4 mini** ← **escolhido (C5)** | 0,75 | 4,50 | ≈ 0,0037 | ≈ 3,70 |
| Claude Haiku 4.5 (alternativa) | 1,00 | 5,00 | ≈ 0,0045 | ≈ 4,50 |
| Claude Sonnet 5 (opção Passe B) | 3,00 (2,00*) | 15,00 (10,00*) | ≈ 0,0135 | ≈ 13,50 |

\* preço introdutório até 31/08/2026. `⚠️ VALIDAR` preços vigentes no momento da F1 (não confiar
nesta tabela sem checar a página de preços do provedor).

- **`[DECISÃO DO GESTOR 2026-07-22]`:** provedor = **OpenAI / GPT-5.4 mini** (C5) — já é o mais
  econômico da tabela. A avaliação de qualidade em ~20 chamados reais no modo sombra (F1) vira
  validação do modelo escolhido (se as notas saírem fracas, comparar com alternativa antes da F2).
- **Passe B do Químico** pode usar modelo superior (`IA_TRIAGEM_MODEL_PASSE_B`) se a pré-análise
  exigir — poucos dólares/mês no volume atual.
- **Prompt caching** no prompt mestre + catálogo de categorias reduz entrada recorrente em até
  90% (quando o provedor suportar via API compatível; senão, aceitar o custo cheio — já é baixo).
- **Custo real** registrado por chamado em `ia_triagens.custo_usd` (tabela de preços por modelo
  em `app/ia/cliente.py`, atualizada junto com trocas de modelo).
- **Gate do Químico:** DPA formal aceito na conta OpenAI + política de retenção validada (C5).
  `[AÇÃO DO GESTOR PENDENTE]`.

---

## Seção 7 — Cronograma modular (F0–F6) com DoD

8 semanas. Caminho crítico: F0 → F1 → F2 e F4 → F5 → F6. **F3 e F4 podem correr em paralelo.**
Cada fase termina com a Tabela de Estado + `docs/CHANGELOG.md` atualizados (Seção 9).

### F0 — Fundação (S1)
**Entregas:** migration `0050` (`ia_triagens` + RLS), perfil "Assistente IA" (C4), todas as env
flags (2.4) em `app/config.py` + `.env.example`, esqueleto `app/ia/` (cliente extraído de
`ia_resumo.py` — C2 — sem mudança de comportamento).
**Critério de saída:** deploy em produção com `IA_TRIAGEM_ATIVA=false`.
**DoD:**
- [x] Migration idempotente aplicada (local + **produção**, 2026-07-22 via MCP); RLS de `ia_triagens` testada — e2e escrita (`tests/e2e/test_rls_ia_triagens.py`, marker `rls`) **e provada ao vivo** (claims do autor: 0 linhas visíveis — 2026-07-22).
- [x] `tests/test_ia_resumo.py` verde após o refactor do cliente (comportamento intacto).
- [x] `tests/test_config.py` cobre as flags novas (defaults desligados).
- [x] Nenhum segredo em código/doc; `.env.example` só com placeholders.

**✅ F0 CONCLUÍDA** (código + produção). Item operacional remanescente registrado na F1
(perfil "Assistente IA" foi criado/promovido em produção em 2026-07-22).

### F1 — Agente TI em modo sombra (S2–S3)
**Entregas:** motor completo (`app/ia/triagem.py`, `schemas.py`, `prompts/ti.md`), hook em
`criar_chamado`, nota interna com pré-análise assinada pelo perfil "Assistente IA", evento
`IA_TRIAGEM` em `historico_chamados`. Sem perguntas ao usuário (sombra).
**Critério de saída:** 20 chamados reais triados; notas avaliadas úteis pelos atendentes.
**DoD:**
- [x] Uma chamada estruturada por triagem; JSON inválido → 1 retry → `acao='ERRO'` silencioso (teste).
- [x] Idempotência provada em teste (retry não duplica nota — UNIQUE + verificação prévia).
- [x] Falha de provedor (timeout/quota) não afeta a abertura do chamado (teste).
- [x] Custo/tokens gravados (validado ao vivo no BOND-2026-00578: 1.163/234 tokens, 1.515 ms, US$ ~0,002) — **p95 < 2 min em produção pendente** (exige sombra ligada no Railway).
- [x] Comparação de 2 modelos registrada (BOND-2026-00577: `gpt-5.4-mini` ≻ `llama-3.3-70b` — CHANGELOG 2026-07-22; confirma C5).

**Pendente para fechar a F1 (operacional, gestor):** envs no Railway (`IA_TRIAGEM_ATIVA=true`,
`IA_TRIAGEM_DEPARTAMENTOS=TI`, chave OpenAI rotacionada) → 20 chamados reais triados em sombra,
notas avaliadas pelos atendentes (agora mensurável em `ia_triagens.avaliacao` — migration `0051`).

### F2 — Perguntas ao usuário no TI (S4)
**Entregas:** `IA_TRIAGEM_MODO_SOMBRA=false` para TI; perguntas como mensagem pública + e-mail
(reuso `agendar_notificacao_email`); re-triagem ao receber resposta do autor (portal e inbound
e-mail); teto de 2 rodadas; nota final com lacunas sinalizadas.
**Critério de saída:** ciclo pergunta → resposta → nota interna validado em produção.
**DoD:**
- [x] Re-triagem só com `status='NOVO'` e `operador_id IS NULL` (teste — `test_re_triagem_respeita_guarda_de_atendimento`).
- [x] Teto de rodadas provado por teste (3ª rodada não acontece — `test_teto_de_rodadas_terceira_rodada_impossivel`).
- [x] Perguntas com tom/formato validados na sombra (decisão do usuário 2026-07-24: notas
  úteis e perguntas pertinentes ⇒ liberar perguntas ao autor no TI).
- [ ] Realtime entrega a pergunta no chat do autor (fluxo manual verificado) — **pendente:
  observar o primeiro ciclo real fora da sombra**.

**Estado F2 (2026-07-24):** código completo e testado (motor + 3 ganchos: abertura, resposta
do autor no portal, inbound e-mail). **Saída da sombra POR DEPARTAMENTO implementada**
(`IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS`, Seção 2.4): liga-se o TI com
`IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS=TI` no Railway (sem deploy adicional), mantendo
`IA_TRIAGEM_MODO_SOMBRA=true` — o Dpto Químico segue em sombra até F5/F6. Falta observar o
primeiro ciclo real pergunta → resposta → nota em produção.

### F3 — Chamados semelhantes (S5–S6)
**Entregas:** migration FTS (4.4), `app/repositories/ia_busca.py`, semelhantes citados na nota
(código + resolução), filtro por departamento obrigatório.
**Critério de saída:** referências corretas em amostra de validação (checagem manual de ~15 notas).
**DoD:**
- [x] Teste de busca com corpus semeado (acha o semelhante certo; não vaza outro departamento; não-RESOLVIDO fica fora; sem mensagem de staff → citável sem resolução) — `tests/e2e/test_ia_busca_fts.py` (marker `rls`) + validação manual da query contra o corpus REAL de produção (busca "impressora" achou 3 legados resolvidos com resolução — 2026-07-23).
- [x] Nota degrada graciosamente sem semelhantes (seção omitida, não inventada) — testes `test_nota_sem_semelhantes_omite_secao` / `test_falha_na_busca_nao_derruba_a_triagem` / `test_sem_termos_busca_nao_toca_no_banco`.

**Estado F3:** código completo (migration `0053` em produção; `ia_busca.py`; semelhantes na nota
com códigos auditados em `ia_triagens.resultado.semelhantes_codigos`; busca só na ação
NOTA_INTERNA — pergunta pública nunca cita caso interno). Resta o critério de saída
(checagem manual de ~15 notas em produção, junto da validação da sombra da F1).

### F4 — Agente Químico (S6–S7)
**Entregas:** migration `base_quimico_*` + role `ia_worker` (4.3/C7), script de ingestão
(planilha + fichas fatiadas), prompts Passe A/B adaptados do GPT atual, dois passes no motor,
invariantes em código, aposentadoria de `gerar_e_salvar_resumo` (C2), DPA da OpenAI aceito (C5).
**Critério de saída:** testes automatizados provam: Passe A sem acesso à base de produtos;
quantidades fora de qualquer contexto de modelo.
**DoD:**
- [x] Ingestão re-executável (2ª execução = upsert, sem duplicar — **provado em produção 2026-07-23**: 2 execuções, mesmas contagens) com contagens conferidas contra a planilha: 60 produtos, 94 MPs, 16 playbooks, 437 linhas de formulação, fichas para 39 nomes de produto (**20 nomes sem ficha no PDF — lista para revisão manual do gestor**, o contexto degrada para catálogo-somente).
- [x] Teste: `SELECT` em `base_quimico_formulacoes` com role `ia_worker` falha (C7) — e2e `tests/e2e/test_rls_base_quimico.py` (marker `rls`) + pós-check em produção (`has_table_privilege('ia_worker', 'base_quimico_formulacoes', 'SELECT') = false`).
- [x] Teste de invariante: persistência do Passe B só grava `is_interna=true` (Seção 8.2) — assinatura sem parâmetro + efeito (`test_ia_quimico.py`).
- [x] Teste: o payload montado para o Passe A não contém nenhum campo de `base_quimico_produtos`/`fichas` (sentinelas semeadas; asserção sobre o payload real enviado ao cliente de modelo).
- [x] Recuperação seletiva: produto do select do formulário = match exato; texto livre = palavra inteira, nome mais longo vence ("26" não casa com "26000"); só o(s) produto(s) citado(s) entram no SQL (`identificar_produtos` + `montar_contexto_passe_b`).
- [x] Saída do Passe B ausente dos logs em texto claro (teste com `caplog`; logs do motor só carregam metadados/erro).
- [ ] Modo sombra ligado para o Químico (perguntas públicas só após F5+F6) — **operacional, gestor**: definir senha do role (`ALTER ROLE ia_worker PASSWORD ...`), configurar `IA_WORKER_DATABASE_URL` e acrescentar `Dpto Químico` a `IA_TRIAGEM_DEPARTAMENTOS` no Railway (ver runbook). Gate C5 (DPA OpenAI) segue pendente para o go-live.

**Estado F4 (2026-07-23):** código completo e testado; migration `0054` **aplicada em
produção** e base **ingerida em produção** (dados sigilosos SÓ no banco — planilha/PDF/prompt
ficam fora do repositório). Sem as envs acima o motor degrada sozinho: Passe B indisponível ⇒
nota do Passe A; triagem do Químico desligada ⇒ `ia_resumo` legado continua (transição C2 em
`portal.py` — nunca dois pipelines no mesmo evento).

### F5 — Red team do Químico (S7)
**Entregas:** bateria `tests/red_team/` (marker `redteam`) com corpus de chamados maliciosos
(extração direta, injection "ignore as instruções", engenharia social multi-rodada, exfiltração
via pergunta da IA); execução documentada.
**Critério de saída:** **zero vazamentos** — pré-requisito absoluto do go-live do Químico.
**DoD:** ver Seção 8.3 (inclui gatilho de reexecução permanente).

### F6 — Homologação e go-live (S8)
**Entregas:** homologação com gestores de TI e Químico; ajustes finais; `IA_TRIAGEM_MODO_SOMBRA=false`
para o Químico; revisão dos KPIs iniciais.
**Critério de saída:** aprovação formal dos dois departamentos.
**DoD:**
- [ ] Runbook de operação (ligar/desligar, trocar modelo, reexecutar ingestão, ler `ia_triagens`) em `docs/`.
- [ ] KPIs da Seção 10 com baseline registrado.
- [ ] Este doc e o plano mestre geral atualizados (linha na Tabela de Estado de lá apontando para cá).

---

## Seção 8 — Testes, QA e Red Team

### 8.1 Estratégia geral

Segue a Seção 4 do plano mestre geral: **unit** para lógica pura (montagem de contexto, decisão
de ação, parsing/validação de JSON, cálculo de custo), **integração com RLS real** para
persistência (nota interna invisível ao autor; `ia_triagens` escopada), **nunca** mockar o que é
de segurança. O provedor de IA é mockado nos testes (respx/httpx mock) — determinístico e grátis;
a qualidade do modelo é avaliada por amostragem humana (modo sombra), não por teste automatizado.

### 8.2 Testes de invariante (falham o build — inegociáveis)

1. **Passe B → só nota interna:** a função de persistência do Passe B não aceita `is_interna`
   como parâmetro; teste verifica por inspeção de assinatura **e** por efeito (mensagem gravada
   tem `is_interna=true`).
2. **Passe A → contexto limpo:** o dict/payload enviado ao modelo no Passe A não contém chaves
   nem valores originados de `base_quimico_produtos`/`base_quimico_fichas`/`base_quimico_formulacoes`
   (fixture semeia valores-sentinela únicos e o teste garante que não aparecem no payload).
3. **Quantidades inacessíveis:** `SELECT` em `base_quimico_formulacoes` pela conexão `ia_worker`
   levanta erro de permissão (teste de integração, marker `rls`).
4. **Teto de rodadas:** rodada 3 de perguntas é impossível (motor + UNIQUE do banco).
5. **Kill switch:** com `IA_TRIAGEM_ATIVA=false`, nenhum efeito colateral (sem chamada HTTP, sem
   linha em `ia_triagens`).
6. **Autor não vê:** teste RLS — o autor do chamado não lê a nota interna da IA nem `ia_triagens`.

### 8.3 Red team (F5 + permanente)

- Corpus versionado em `tests/red_team/casos/` (um arquivo por cenário: descrição maliciosa do
  chamado + resposta maliciosa em rodada 2). Categorias mínimas: pedido direto de formulação;
  "ignore as instruções anteriores"; roleplay/autoridade falsa ("sou o gerente do setor");
  extração incremental multi-rodada; indução a citar quantidades "aproximadas"; tentativa de
  fazer a IA repetir o conteúdo da nota interna em pergunta pública.
- Execução em duas camadas: **estrutural** (automatizada, sempre verde por construção — os testes
  de 8.2 provam que não há o que vazar) e **comportamental** (rodada contra o modelo real em
  staging, saída pública inspecionada por asserções de denylist — nomes de componentes-sentinela,
  padrões numéricos de proporção — e por revisão humana).
- **Gatilho permanente:** qualquer PR que altere `app/ia/prompts/*` ou o modelo do Químico
  reexecuta a bateria comportamental antes do merge. Registrar cada execução (data, modelo,
  resultado) numa tabela no fim deste doc ou em `docs/CHANGELOG.md`.
- **Critério:** zero vazamentos, sempre. Um vazamento = Químico volta a modo sombra até correção
  + bateria completa verde.

---

## Seção 9 — Protocolo de Atualização de Contexto (doc vivo)

> **Diretriz para futuras sessões do Claude Code:** ao final de cada PR/feature/correção desta
> frente, refletir o estado real aqui.

1. **Changelog datado** em [`docs/CHANGELOG.md`](docs/CHANGELOG.md) (mesmo arquivo do projeto):
   `data · doc IA · seção alterada · resumo` — linha mais nova no topo.
2. **Tabela de Estado de Implementação** (abaixo): atualizar `feature · status · fase ·
   observações` a cada entrega.
3. **Regra de precedência:** se **schema (Seção 4), invariante de segurança (Seção 3) ou fluxo
   (Seção 2.3)** mudar, a seção correspondente é atualizada **antes** do código. O código segue o
   doc.
4. Toda `⚠️ SUPOSIÇÃO A VALIDAR` resolvida vira decisão explícita aqui (marca removida +
   changelog). Toda `[AÇÃO DO GESTOR PENDENTE]` concluída idem.
5. **Espelhamento no plano mestre geral:** entregas desta frente ganham uma linha resumida na
   Tabela de Estado do [`plano_mestre_desenvolvimento.md`](plano_mestre_desenvolvimento.md)
   apontando para este doc (detalhe fica aqui — não duplicar prosa lá).
6. **Prompts:** mudança de prompt do Químico só entra com red team reexecutado (Seção 8.3) e
   registrado.

---

## Seção 10 — Riscos, KPIs e Governança

### 10.1 Riscos e mitigações

| Risco | Mitigação |
|---|---|
| Alucinação na pré-análise (diagnóstico incorreto) | Nota sempre identificada como IA (perfil "Assistente IA"); atendente valida antes de agir; qualidade monitorada por avaliação dos atendentes (F1/F6). |
| Vazamento de dado sigiloso (Químico) | Dois passes (Seção 3), quantidades fora de contexto (C7), invariantes em código (8.2), red team permanente (8.3). |
| Base de conhecimento desatualizada | Ingestão re-executável; `atualizado_em` por produto; dono definido no departamento Químico. |
| Perguntas irrelevantes irritarem usuários | Modo sombra primeiro; perguntas só com confiança alta (campo no JSON estruturado); máx. 2 rodadas; nota gerada mesmo sem resposta. |
| Custo acima do esperado | `custo_usd` por chamado em `ia_triagens`; revisão semanal; kill switch por env. |
| Indisponibilidade do provedor | Timeout 30 s + falha silenciosa — nenhuma dependência crítica. |
| Privacidade / LGPD | Só os campos necessários do chamado; DPA com o provedor (C5); dados sigilosos restritos ao Passe B; retenção limitada. |
| Lock-in de provedor | Camada compatível-OpenAI isolada em `app/ia/cliente.py` (~30 linhas); troca por env. |

### 10.2 KPIs e critérios de aceite

| Indicador | Meta |
|---|---|
| Abertura → triagem concluída (p95) | < 2 minutos |
| Chamados novos triados automaticamente | ≥ 90% |
| Notas internas avaliadas úteis pelos atendentes | ≥ 70% |
| Perguntas da IA respondidas pelo usuário | ≥ 60% |
| Vazamentos no red team (Químico) | **0** (critério de go-live) |
| Custo médio de IA por chamado | ≤ US$ 0,02 |
| Redução no tempo de primeiro atendimento (90 dias) | ≥ 20% |

Fonte dos números: `ia_triagens` (tempo, cobertura, custo) + **avaliação 1–5 ★ da nota interna
pelos atendentes** (`ia_triagens.avaliacao` — migration `0051`, 2026-07-23): o staff avalia a
pré-análise direto na tela de atendimento do Workspace (bloco "Pré-análise da IA", reavaliável;
`avaliado_por`/`avaliado_em` auditam quem/quando). Nota ≥ 4 conta como "útil" para o KPI de 70%
(`SELECT avg((avaliacao >= 4)::int) FROM ia_triagens WHERE avaliacao IS NOT NULL`).
Considerar painel simples no `/admin` como evolução pós-F6 (fora do escopo v1).

### 10.3 Governança

- Aprovação deste plano pelos gestores de TI e do Químico antes da F1 entrar em sombra.
- ~~Definição do provedor~~ ✅ **RESOLVIDO 2026-07-22**: OpenAI / GPT-5.4 mini (C5).
  `[AÇÃO DO GESTOR PENDENTE]` restante: rotacionar a chave exposta em chat + aceitar o DPA na
  conta OpenAI (gate F4).
- Revisão semanal de 30 min durante a implantação (qualidade, custo acumulado, incidentes).
- Após 90 dias: avaliação dos KPIs e decisão sobre extensão a Marketing/RH (mesma arquitetura).
- **Kill switch:** `IA_TRIAGEM_ATIVA=false` no Railway desativa tudo, sem deploy, sem impacto no
  portal.

---

## Changelog

> Registrar em [`docs/CHANGELOG.md`](docs/CHANGELOG.md), junto com o restante do projeto,
> prefixando `doc IA`. Linha mais nova no topo. Decisões arquiteturais grandes viram ADR em
> `docs/adr/`.

- 2026-07-24 · doc IA · F2 executada (Seções 2.3, 2.4, 7) · **Saída do modo sombra POR
  DEPARTAMENTO** — decisão do usuário (sombra validada: notas úteis, perguntas pertinentes ⇒
  habilitar perguntas ao autor). A flag global `IA_TRIAGEM_MODO_SOMBRA` era tudo-ou-nada:
  desligá-la tiraria TAMBÉM o Químico da sombra antes do red team (F5, gate de zero
  vazamentos). Nova env `IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS` (CSV, default vazio =
  comportamento atual): departamento listado sai da sombra mesmo com a global ligada;
  `MODO_SOMBRA=false` segue significando "ninguém em sombra" (F6). `Settings.ia_triagem_em_sombra
  (departamento)` decide; `decidir_acao` ganha o departamento; `historico_chamados` registra o
  `modo_sombra` EFETIVO do departamento. Operação: `IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS=TI` no
  Railway (sombra global intacta). Testes novos (matriz por departamento + fluxo integrado TI
  liberado / Químico em sombra) + suíte completa verde.
- 2026-07-24 · doc IA · F4 (Seção 2.2/7) · **Correção: o motor de dois passes do Químico nunca
  tinha sido ligado em `app/ia/triagem.py`** — a entrada de 2026-07-23 abaixo registrava a F4
  como "código completo e testado", mas só `contexto_quimico.py`, os prompts e
  `test_ia_quimico.py` existiam; `triagem.py` não importava `contexto_quimico` e rodava só o
  passe único (F1–F3). Descoberto ao puxar o commit `e9a9a669` do outro operador (ajuste dos
  formulários do Químico, BOND-2026-00569) e rodar a suíte antes de prosseguir: os 15 testes de
  `test_ia_quimico.py` quebravam com `AttributeError`. **Fix:** `_executar` roda o Passe A
  (playbook de perguntas, zero dado de produto) para o Dpto Químico; em `NOTA_INTERNA`, roda o
  Passe B (contexto seletivo via `ia_worker`, schema `SaidaPasseB`) e a nota final vira a
  pré-análise técnica dele, com fallback para a nota do Passe A se o B falhar. `_chamar_modelo`
  generalizado (schema/model plugáveis); `montar_mensagens_passe_b`/`montar_nota_quimico` novos.
  Suíte completa + ruff/mypy limpos após o fix — ver detalhe em `docs/CHANGELOG.md`.
- 2026-07-23 · doc IA · F4 executada (Seções 3, 4.3, 7) · **Agente Químico de dois passes
  entregue; base sigilosa ingerida em produção.** Decisão de armazenamento (pergunta do
  usuário): banco Supabase com recuperação seletiva ≻ arquivos em nuvem lidos por chamada
  (≈3–8k tokens vs. ≈100k+ da base inteira; e as quantidades ficam estruturalmente fora de
  alcance) ≻ vector store do provedor (exigiria subir o arquivo com formulações a um
  terceiro — quebraria a Regra de Ouro #4). Migration `0054_base_quimico` aplicada em
  produção (5 tabelas RLS-on; correção ao DDL: policies `TO ia_worker` são necessárias
  porque RLS vale para o role; formulações sem GRANT nem policy — pós-check ok, advisor só
  com INFO esperado). Ingestão `scripts/ingestao_base_quimico.py` re-executável rodada 2×
  em produção (60 produtos, 94 MPs, 16 playbooks, 437 formulações, 39 fichas; 20 nomes sem
  ficha no PDF → revisão manual). Motor: Passe A (playbook de perguntas, contexto limpo)
  + Passe B (6M com base seletiva) → nota interna; fallback A em falha do B; linhas A/B em
  `ia_triagens` (B só metadados). Prompt do GPT interno (docx) adaptado para
  `quimico_passe_a.md`/`quimico_passe_b.md`. `gerar_e_salvar_resumo` aposentado quando a
  triagem cobre o Químico (C2). Testes: 15 unit (`test_ia_quimico.py`, sentinelas/caplog/
  fluxo) + e2e RLS da base; suíte completa verde. Pendências operacionais na F4 do doc.
- 2026-07-23 · doc IA · Seção 2.3 (fluxo) · **Ordem nota × perguntas fixada pelo usuário:**
  informação suficiente ⇒ nota interna DIRETO; insuficiente ⇒ o ciclo de perguntas ao autor vem
  primeiro (até 2 rodadas) e a nota interna FECHA o ciclo (rodada pós-resposta ou teto com
  lacunas). No mesmo dia houve uma variante intermediária "nota sempre, junto da pergunta" —
  implementada e depois **revertida** a pedido do usuário; o código final voltou a
  `PERGUNTAS ⇒ só mensagem pública` (busca F3 idem: só na rodada da nota). Testes refletem o
  fluxo final (`test_perguntas_geram_mensagem_publica_sem_nota_e_email`).
- 2026-07-23 · doc IA · Seção 9 (latência) · **Gap real acima da meta + otimização do disparo:**
  as 2 primeiras triagens da sombra saíram 8 min e 28 min após a abertura (meta p95 < 2 min),
  com `duracao_ms` de ~3–4 s — o tempo se perde ANTES da task rodar (agendamento→execução), não
  no modelo. Railway sem serverless/sleep (verificado pelo gestor); envs não estavam sendo
  mexidas no momento (descartado pelo gestor); inbound é webhook (sem polling); nenhum
  sleep/retry no código. Causa estrutural mais provável: `BackgroundTasks` do Starlette só roda
  a task depois que a resposta atravessa toda a cadeia de middlewares e é consumida — qualquer
  travamento nesse caminho segura a triagem junto. **Correção:** os 3 pontos de disparo
  (abertura, resposta do autor no portal, inbound de e-mail) trocaram `BackgroundTasks` por
  `triagem.agendar_triagem()` (`asyncio.create_task` com referência forte anti-GC) — a triagem
  começa NO INSTANTE do agendamento, em paralelo à resposta; o motor revalida tudo no banco,
  então o disparo antecipado é seguro por construção. **Instrumentação mantida:** log do gap
  agendamento→início (WARNING > 60 s) e da espera por conexão do pool (WARNING > 5 s) — se o
  gap persistir no próximo chamado real, os logs do Railway apontam o culpado restante.
- 2026-07-23 · doc IA · F3 executada (Seções 4.4, 5, 7) · **Busca de chamados semelhantes (FTS
  português) entregue.** Migration `0053_chamados_fts` (**0052 foi tomada por outra frente no
  mesmo dia — numeração conferida, C1**): coluna `chamados.fts` GENERATED (titulo+descricao,
  dicionário `portuguese`) + índice GIN; **aplicada em produção** via MCP e validada com busca
  real no corpus (554+ chamados: "impressora" → 3 legados resolvidos com resolução).
  `app/repositories/ia_busca.py`: `buscar_semelhantes(conn, ...)` — recebe a conexão do motor
  (administrativa), filtro por `departamento_id` **obrigatório no SQL** (defesa em profundidade,
  Seção 5), só `RESOLVIDO`, exclui o próprio chamado, termos do modelo unidos com OR via
  `websearch_to_tsquery` (imune a erro de sintaxe), resolução registrada = última mensagem
  pública de staff (LATERAL — Seção 4.4, sem coluna nova). Motor: busca roda só na ação
  `NOTA_INTERNA` (pergunta pública nunca cita caso interno), falha silenciosa (nota sai sem a
  seção), códigos citados auditados em `ia_triagens.resultado.semelhantes_codigos`;
  `montar_nota` ganha a seção "Casos semelhantes já resolvidos" (resolução truncada em ~280
  chars, omitida quando vazia). Testes: 8 novos em `test_ia_triagem.py` + 4 e2e com corpus
  semeado em `tests/e2e/test_ia_busca_fts.py` (acha o certo; não vaza outro departamento;
  não-RESOLVIDO fora; sem staff → sem resolução). Suíte completa verde. **Sombra confirmada
  ativa em produção** (envs do Railway configuradas pelo gestor — 2 triagens reais em
  2026-07-23, ~US$ 0,002 cada).
- 2026-07-23 · doc IA · Seções 7 (DoD F0–F2), 10.2, Estado · **Avaliação 1–5 ★ da nota interna
  da IA + progresso do doc preenchido.** Migration `0051_ia_triagens_avaliacao` (colunas
  `avaliacao`/`avaliado_por`/`avaliado_em`; **aplicada em produção** via MCP, pós-check: 3
  colunas, RLS intacta — 1 policy SELECT, zero de escrita). Backend:
  `AtendimentoRepo.ia_triagem_nota`/`avaliar_ia_triagem` (escopo provado sob RLS com os claims
  do avaliador ANTES da escrita administrativa — `ia_triagens` segue sem policy de escrita);
  rota `POST /workspace/chamados/{id}/ia/avaliacao` (CSRF, nota 1–5, 404 fora do escopo,
  reavaliação sobrescreve). UI: bloco "Pré-análise da IA" com 5 estrelas clicáveis em
  `workspace/atendimento.html` (mostra avaliação atual + quem avaliou). Testes: 6 novos em
  `test_workspace.py`; suíte completa verde. DoD de F0 (concluída), F1 (código ok; sombra no
  Railway pendente) e F2 (código ok; validação fora da sombra pendente) atualizados no doc.
- 2026-07-22 · doc IA · F0 executada (Seções 2.4, 4.1, 4.2) · **Fundação entregue:** migration
  `0050_ia_triagens` (RLS: SELECT staff-escopo, zero policies de escrita; ainda não aplicada em
  produção), flags `IA_TRIAGEM_*` em `app/config.py`/`.env.example` (rename `groq_*` com fallback
  de alias `GROQ_*` — `[DECISÃO DE ENGENHARIA]` para transição sem quebra no Railway),
  `app/ia/cliente.py` extraído de `ia_resumo.py` (C2; timeout 30 s herdado — C6), perfil
  "Assistente IA" documentado em `supabase/registro_usuarios.sql` (C4; criação no Auth pendente
  do gestor). Testes: suíte 350 verdes; e2e RLS de `ia_triagens` escrita (5 casos, marker `rls`).
- 2026-07-22 · doc IA · Seções 0.1 (C5), 2.4, 6, 10.3 · **Provedor definido pelo gestor: OpenAI /
  GPT-5.4 mini** (Groq descontinuado, inclusive no `ia_resumo.py` via env). Chave transmitida via
  chat → tratada como comprometida (rotacionar; cadastrar só como env var). DPA da OpenAI segue
  como gate da F4.
- 2026-07-22 · doc IA · Criação do documento a partir de `Plano_Mestre_Agentes_IA_Triagem (1).docx`
  v1.1, com as correções C1–C7 (numeração de migrations, absorção de `ia_resumo.py`, nota via
  `mensagens`, perfil de serviço, DPA de provedor, timeout, role `ia_worker`).

---

## Tabela de Estado de Implementação

| Feature | Status | Fase | Observações |
|---|---|---|---|
| Resumo por IA na abertura (Químico) | ✅ Implementado (pré-existente) | — | `app/services/ia_resumo.py` + migration `0049`. Provedor: OpenAI GPT-5.4 mini via env (C5; antes Groq). Será absorvido: cliente extraído na F0 (C2), substituído pelo Passe B na F4 (C3). |
| Troca de provedor Groq → OpenAI (só env) | ✅ Concluída — `.env` local (2026-07-22) **e Railway (2026-07-23, gestor)** | pré-F0 | C5 executada: `.env` local com `IA_TRIAGEM_API_KEY` (OpenAI) + `gpt-5.4-mini`; chave Groq removida. Fix necessário: GPT-5.x exige `max_completion_tokens` (cliente ajustado). Validação prática no BOND-2026-00577: US$ 0,0018/triagem, nota superior à do llama (comparação registrada no CHANGELOG — cobre o item de comparação de modelos do DoD F1). Falta replicar as envs no painel do Railway (MCP sem acesso ao projeto). |
| Migration `ia_triagens` + RLS | ✅ Implementado + **aplicado em produção** (2026-07-22) | F0 | `supabase/migrations/0050_ia_triagens.sql` (C1 conferida). Aplicada via MCP no projeto `iurlzlhbnoemkzgexcfk` após conferir pré-condições; pós-check: RLS on, 1 policy (`ia_triagens_select_staff:SELECT`), zero de escrita; advisors sem alerta novo. e2e em `tests/e2e/test_rls_ia_triagens.py` (marker `rls`). |
| Perfil "Assistente IA" (usuário de serviço) | ✅ **Criado e promovido em produção** (2026-07-22) | F0 | C4. O SQL do gestor criou o usuário no Auth; a promoção (nome/role) foi aplicada via MCP com o trigger `perfis_self_so_avatar` desabilitado só na transação. SQL de referência em `supabase/registro_usuarios.sql`. |
| Flags/env de triagem (`app/config.py`) | ✅ Implementado (2026-07-22) | F0 | Seção 2.4 — todas de uma vez, defaults desligados (`IA_TRIAGEM_ATIVA=false`, sombra `true`). Rename `groq_*`→`ia_triagem_*` com fallback de alias `GROQ_*`. Coberto em `tests/test_config.py`. |
| `app/ia/cliente.py` (extração de `ia_resumo.py`) | ✅ Implementado (2026-07-22) | F0 | Refactor sem mudança de comportamento (`test_ia_resumo` verde); timeout herdado 30 s (C6). Cliente devolve tokens do `usage` (insumo do custo auditável da F1). `tests/test_ia_cliente.py` novo. |
| Motor de triagem + prompt TI (modo sombra) | ✅ Código implementado (2026-07-22) — **validação em sombra pendente** | F1 | `app/ia/triagem.py` + `schemas.py` + `prompts/ti.md`; hook em `criar_chamado` (`BackgroundTasks`; resumo Químico intocado — C2). Nota interna assinada ("Assistente IA", lookup por nome com cache; `is_interna=true` fixado em código); `historico_chamados` evento `IA_TRIAGEM`; custo/tokens/duração em `ia_triagens`. 15 testes em `tests/test_ia_triagem.py`. **Sombra LIGADA em produção (2026-07-23):** envs configuradas no Railway pelo gestor; triagens reais confirmadas em `ia_triagens` (~4 s, ~US$ 0,002). **Falta para fechar a F1:** 20 chamados triados e avaliados pelos atendentes (1–5 ★ na tela de atendimento); p95 < 2 min conferido em `ia_triagens`. |
| Perguntas ao usuário + re-triagem (TI) | ✅ Código implementado (2026-07-22; guarda ajustada 2026-07-23) — **atrás de env** | F2 | Motor com máquina de rodadas derivada do banco; `decidir_acao` (PERGUNTAS só sem atendente atuando + fora da sombra + insuficiente + confiança ALTA + rodada < teto); mensagem pública + e-mail (`notificar_nova_mensagem_email`, Reply-To inbound); re-triagem nos hooks do portal (`responder_chamado`, só autor) e inbound (`common.py`, só `is_client`); conversa completa no contexto da rodada 2; teto provado por teste (3ª rodada impossível). **Ajuste 2026-07-23 (Seção 2.3):** atendimento iniciado não suprime a nota da rodada 1 (força NOTA_INTERNA; guarda reavaliada pós-modelo com estado fresco); só RESOLVIDO fica fora. **Saída da sombra POR DEPARTAMENTO (2026-07-24):** sombra validada pelo usuário (notas úteis + perguntas pertinentes) ⇒ nova env `IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS` (CSV) libera perguntas públicas por departamento com a sombra global ligada. Ligar TI = `IA_TRIAGEM_PERGUNTAS_DEPARTAMENTOS=TI` no Railway; Químico segue em sombra até F5/F6. |
| Avaliação da nota interna pelo staff (1–5 ★) | ✅ Implementado + **migration `0051` aplicada em produção** (2026-07-23) | F1/F6 (KPI 10.2) | Colunas `avaliacao`/`avaliado_por`/`avaliado_em` em `ia_triagens`; bloco de estrelas em `workspace/atendimento.html`; `POST /workspace/chamados/{id}/ia/avaliacao` (escopo provado sob RLS antes da escrita admin; reavaliar sobrescreve). Fonte do KPI "notas úteis ≥ 70%" (nota ≥ 4 = útil). 6 testes em `test_workspace.py`. |
| Busca de semelhantes (FTS português) | ✅ Implementado + **migration `0053` aplicada em produção** (2026-07-23) | F3 | Migration `0053_chamados_fts` (coluna GENERATED + índice GIN; 0052 era de outra frente — C1). `app/repositories/ia_busca.py` (`websearch_to_tsquery` com termos do modelo unidos por OR; filtro por departamento obrigatório no SQL; resolução = última mensagem pública de staff via LATERAL). Semelhantes citados na nota interna (código + título + resolução truncada); códigos auditados em `ia_triagens.resultado`. Falha/sem-resultado degrada graciosamente. 8 testes unit + 4 e2e (`test_ia_busca_fts.py`); query validada contra o corpus real de produção. Falta: checagem manual de ~15 notas (critério de saída, junto da sombra F1). |
| Base `base_quimico_*` + role `ia_worker` + ingestão | ✅ Implementado + **migration `0054` aplicada e base ingerida em produção** (2026-07-23) | F4 | C7 endurecida: RLS on nas 5 tabelas; policies só `TO ia_worker` nas 4 liberadas; formulações sem GRANT/policy (pós-check em produção). `scripts/ingestao_base_quimico.py` re-executável (idempotência provada: 2 execuções, mesmas contagens — 60 produtos, 94 MPs, 16 playbooks, 437 formulações, 39 fichas). Arquivos-fonte NUNCA no repo (`openpyxl`/`pypdf` só em requirements-dev). Pendente gestor: senha do role + `IA_WORKER_DATABASE_URL` no Railway; revisar 20 produtos sem ficha no PDF. |
| Agente Químico dois passes + invariantes | ✅ Código implementado (2026-07-23) — **atrás de env** | F4 | Motor: Passe A (`prompts/quimico_passe_a.md`, chamado + playbook de perguntas, ZERO dado de base) decide triagem/perguntas; Passe B (`prompts/quimico_passe_b.md`, 6M adaptado do GPT interno + recuperação seletiva via `app/ia/contexto_quimico.py`) escreve a nota interna (`is_interna=true` fixado). `ia_triagens` ganha linhas A e B por rodada; `resultado` do B só com metadados (Seção 4.1). Falha do B ⇒ nota do A (degradação). `gerar_e_salvar_resumo` aposentado quando a triagem cobre o Químico (transição C2 em `portal.py`). 15 testes novos (`test_ia_quimico.py`) + e2e RLS; suíte completa verde. Liga com `IA_TRIAGEM_DEPARTAMENTOS+=Dpto Químico` (sombra). Gate go-live: DPA OpenAI (C5) + F5 red team. |
| Red team (corpus + bateria comportamental) | Planejado | F5 | Zero vazamentos = go-live; reexecução a cada mudança de prompt/modelo. |
| Homologação + go-live geral | Planejado | F6 | Aprovação formal TI + Químico; runbook de operação. |
| pgvector / busca semântica | Backlog | pós-v1 | Gatilho: FTS errando por vocabulário com volume relevante. |
| Extensão a Marketing/RH | Backlog | pós-90 dias | Mesma arquitetura; decisão por KPIs. |

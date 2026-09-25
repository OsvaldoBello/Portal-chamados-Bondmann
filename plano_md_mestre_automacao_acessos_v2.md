# Plano Mestre — Automação de Acessos v2 (liderança UBD, Supervisor × Gerente, urgência, correção SAP)

> **Documento vivo.** Evolução da frente descrita em
> [`plano_md_mestre_automacao_acessos.md`](plano_md_mestre_automacao_acessos.md) (v1, em
> produção). As regras da v1 continuam valendo: gate do TI, erro crítico aborta o fluxo,
> alerta detalhado aos admins da TI, licença M365 por 15 dias, credenciais de sistemas
> externos só no worker, contrato em `docs/automacao_api.md`.
>
> **Nada é refatorado.** O portal continua sendo formulário, fila, aprovação, notas e
> alertas; o worker (`Automação/`: `worker.py` + `flow.py`) continua executando nos sistemas
> (M365, UBD, SAP, WMW, SIP). A v2 acrescenta funções, campos e um tipo de job.
>
> **Status:** 🔵 `Projetado` · **Criado em:** 2026-09-25 · **Atualizado em:** 2026-09-25 ·
> **Origem:** brainstorming com o gestor (Osvaldo) após o desligamento parcial de Paola Kemel
> (2026-09-24, etapa SAP ❌).

---

## Sumário

- [Matriz de fases](#matriz-de-fases)
- [Seção 0 — Decisões do gestor](#seção-0--decisões-do-gestor)
- [Seção 1 — F0: Levantamento (somente leitura)](#seção-1--f0-levantamento-somente-leitura)
- [Seção 2 — F1: SAP, usuário interno (bug Paola)](#seção-2--f1-sap-usuário-interno-bug-paola)
- [Seção 3 — F2: Urgência e desligamento às 17h](#seção-3--f2-urgência-e-desligamento-às-17h)
- [Seção 4 — F3: Supervisor × Gerente e vagas na `IB_CO_REGIAO`](#seção-4--f3-supervisor--gerente-e-vagas-na-ib_co_regiao)
- [Seção 5 — F4: Liderança na UBD ao criar usuário](#seção-5--f4-liderança-na-ubd-ao-criar-usuário)
- [Seção 6 — F5: Sincronização de liderança (24 h)](#seção-6--f5-sincronização-de-liderança-24-h)
- [Seção 7 — Contrato v2 (resumo)](#seção-7--contrato-v2-resumo)
- [Seção 8 — Testes, homologação e protocolo de atualização](#seção-8--testes-homologação-e-protocolo-de-atualização)

---

## Matriz de fases

**Legenda:** 🔵 **Projetado** (desenhado e aprovado, sem código) · 🟡 **Em processo**
(código/levantamento em andamento ou aguardando homologação) · 🟢 **Pronto** (em produção e
homologado conforme o DoD).

| Fase | Entrega | Repositório | Depende de | Estado | Atualizado em | Observações |
|---|---|---|---|---|---|---|
| **F0** | Levantamento: schema da `regioes`, `IB_CO_REGIAO`, lista de equipes, usuário `PAOLAK`, e-mails dos gerentes, grupos de Gerência, API de líderes da UBD | — | — | 🟢 Pronto | 2026-09-25 | Só leitura (Seção 1); #2 resolvido na F0b: gerente = `U_IB_CodCom4` (V3 revisada) |
| **F1** | Correção SAP: busca por `$filter`, bloqueio por `InternalKey`, código `PAOLAK`, colisão na criação | worker | — | 🟡 Em processo | 2026-09-25 | Resolve o caso Paola |
| **F2** | Checkbox "Urgência" no desligamento + horário padrão 17h | portal | — | 🟡 Em processo | 2026-09-25 | Independente; pode ir junto com F1 |
| **F3** | Perfil GERENTE; campos Equipe/Gerência correspondente; ocupar/devolver vaga na `IB_CO_REGIAO`; grupos de Gerência; contrato v2 | portal + worker | F0 | 🔵 Projetado | 2026-09-25 | Corrige o `RH2020` gravado em supervisor |
| **F4** | Liderança na UBD na criação (representante, supervisor, interno); campo "Gerência responsável" | portal + worker | F0, F3 | 🔵 Projetado | 2026-09-25 | Corrige o `gestor_email` ignorado |
| **F5** | Job `SINCRONIZAR_LIDERANCA` diário e por evento; espelho com proteção | portal + worker | F4 | 🔵 Projetado | 2026-09-25 | Estreia em modo relatório |

### Detalhamento por item

| Fase | Item | Estado |
|---|---|---|
| F0 | #1 Schema e cadência da `regioes` | 🟢 Pronto |
| F0 | #2 `IB_CO_REGIAO`: supervisor = `CodCom3`, gerente = `CodCom4` (F0b) | 🟢 Pronto |
| F0 | #3 Lista completa de equipes e gerências (`RH20xx`) | 🟢 Pronto |
| F0 | #4 Usuário `PAOLAK` e padrão de código SAP | 🟢 Pronto |
| F0 | #5 E-mails dos 8 gerentes internos (confirmados pelo gestor) | 🟢 Pronto (7/8; 1 a confirmar) |
| F0 | #6 Grupos de Gerência no M365/Exchange | 🟢 Pronto — grupo `Gerencia` (V10) |
| F0 | #7 API UBD: formato de `leaders`, leitura e `PATCH` de líderes | 🟢 Pronto |
| F1 | `find_internal_user` por `$filter` | 🟡 Em processo |
| F1 | Bloqueio via `PATCH Users(<InternalKey>)` | 🟡 Em processo |
| F1 | Criação com código `PRIMEIRONOME + inicial` e colisão | 🟡 Em processo |
| F1 | Homologação: reexecução real da etapa SAP da Paola | 🔵 Projetado |
| F2 | Tipo de campo `checkbox` no motor de formulários | 🟡 Em processo |
| F2 | Campo `urgente` + agendamento imediato | 🟡 Em processo |
| F2 | Padrão 17h | 🟡 Em processo |
| F2 | Selo "Urgente" no card | 🟡 Em processo |
| F3 | Catálogo estático de equipes e gerências | 🔵 Projetado |
| F3 | Formulário: perfil Gerente, campos `equipe` / `gerencia` | 🔵 Projetado |
| F3 | Worker: `GERENTE`, grupos, `occupy_vacancy` / `release_vacancy` | 🔵 Projetado |
| F3 | Contrato v2 (worker aceita 1 e 2 → portal emite 2) | 🔵 Projetado |
| F4 | Leitura somente-SELECT da `regioes` no portal | 🔵 Projetado |
| F4 | Campo "Gerência responsável" + mapa de gerentes | 🔵 Projetado |
| F4 | `lideres_ubd` resolvido no clique do TI e exibido no card | 🔵 Projetado |
| F4 | Worker envia `leaders` (novo e existente) | 🔵 Projetado |
| F5 | Migration `0092` (tipo novo, `chamado_id` nulo, RLS) | 🔵 Projetado |
| F5 | Gatilho diário 06h + gatilho por evento com atraso | 🔵 Projetado |
| F5 | `run_leadership_sync_flow` no worker | 🔵 Projetado |
| F5 | Modo relatório revisado pelo gestor → modo aplicar | 🔵 Projetado |

---

## Seção 0 — Decisões do gestor

Texto fixo (2026-09-25).

| # | Decisão |
|---|---|
| **V1** | A tabela `regioes` (Supabase de outro projeto da Bondmann) **se auto-atualiza a partir do SAP** e é **SOMENTE LEITURA**: nenhum INSERT/UPDATE/DELETE/DDL, nem em teste. |
| **V2** | Criação e desligamento de supervisor e gerente **atualizam a `IB_CO_REGIAO`**, como a automação já faz para representante. |
| **V3** | Os PNs `RH20xx` são **marcadores de vaga** (como `RH2020` é "sem representante"). Na criação, o PN (CardCode) da pessoa **substitui** o marcador em todas as regiões da equipe ou gerência; no desligamento, o marcador **volta**. Supervisor = `U_IB_CodCom3`; gerente = **`U_IB_CodCom4`** (F0b, 2026-09-25 — `CodCom2` não é usado). Hoje os dois campos guardam o **PN da pessoa** (ex.: `RH2004 BRUNO TIARA DA SILVA` como gerente em 54 regiões) e o marcador só aparece quando a vaga está aberta (ex.: `RH2021 EQUIPE MG 2`). Marcadores de gerência: `RH2005 GERENTE SP`, `RH2033 GERENTE MG/RJ`. |
| **V4** | Sincronização de liderança = **espelho com proteção**: remove só os líderes que constam na `regioes` como supervisor ou gerente de alguma região; preserva os líderes postos à mão. |
| **V5** | Na UBD, o **supervisor** tem como líder o gerente das regiões da equipe; o **gerente** fica sem líder. A sincronização cobre **representantes e supervisores**. |
| **V6** | Interno: campo novo e obrigatório **"Gerência responsável"** (8 gerências + "Outra"; "Outra" ⇒ `gestor_email` obrigatório). |
| **V7** | Abordagem: **o portal lê a `regioes` e resolve os líderes; o worker aplica na UBD**. A sincronização é mais um tipo de job na fila existente. |
| **V8** | Na F0, o SAP é consultado pela **Service Layer da automação** (`SAPService`, via túnel), somente GET. |
| **V9** | Fora de escopo: WMW para supervisor e gerente (o fluxo WMW continua só para representante). |
| **V10** | Grupo M365 do gerente: **`Gerencia`** (`9bc94d0a-8dca-4f3e-86fe-44cbb7692ee9`, `bd.gerencia@bondmann.com.br`) — gestor, 2026-09-25. Supervisor continua em `Supervisão` + `BD Supervisão` (já no código). |
| **V11** | Usuário SAP no desligamento de interno (gestor, 2026-09-25): código confere e o nome no SAP é **parecido** ⇒ bloqueia; código confere com nome **não parecido** (e nada por e-mail/nome) ⇒ etapa FAILED "verificar manualmente" com os candidatos; nenhum usuário ⇒ "nada a bloquear". Entregue no worker v1.2.1. |
| **V12** | Leitura da `regioes` pelo portal: **chave anon/publishable via PostgREST (GET)** fornecida pelo gestor. Hoje ela autentica mas devolve 0 linhas (RLS sem política de SELECT para `anon`) — **pré-requisito do gestor**: o dono do projeto cria a política de leitura. O portal nunca escreve (V1). |
| **V13** | Simulação (dry-run) gera só a nota interna — sem mensagem pública ao RH nem mudança de status; a descrição automática do desligamento cita a urgência (gestor, 2026-09-25; entregue). |

### Gerências de internos (V6)

| Gerência (opção do formulário) | Gerente |
|---|---|
| Compras | Alessandro Lodion |
| Controladoria | Anderson Viana |
| PCP / Recebimento / Expedição | Elias **Kirsten** (confirmado pelo gestor; usuário SAP `KIRSTEN`) |
| Laboratório / Químico | Guilherme Rosa |
| RH | Mariana Silva |
| Comercial | Patricia Alves |
| Financeiro | Thiago Rodrigues |
| Filial | Rogério Rossini |
| Outra | — (usa `gestor_email`, obrigatório) |

E-mails (F0 #5; Elias confirmado pelo gestor em 2026-09-25): Alessandro `alessandro@`, Anderson `anderson@`, Elias `elias@`, Guilherme `guilherme.rosa@`, Mariana `mariana.silva@`, Patricia `patricia.alves@`, Thiago `thiago.rodrigues@`, Rogério `rogerio@` (todos `@bondmann.com.br`). A F4 versiona esta tabela; o gestor revisa o PR.

---

## Seção 1 — F0: Levantamento (somente leitura)

Scripts descartáveis no scratchpad. Nenhuma escrita em sistema nenhum; o único produto é
este documento atualizado com as respostas.

| # | Pergunta | Como | Vira |
|---|---|---|---|
| 1 | Schema da `regioes`: há coluna que identifique o representante (e-mail/CardCode)? Quais colunas trazem o e-mail do supervisor e do gerente? Há `updated_at` (cadência da auto-atualização)? | `SELECT` com a credencial do `SUPABASE representantes.txt` (preferir a *anon*; se for *service_role*, só SELECT e registrar o risco) | colunas usadas na F4/F5; valor de `AUTOMACAO_SYNC_ATRASO_MIN` |
| 2 | `IB_CO_REGIAO`: `U_IB_CodCom2` = gerente e `U_IB_CodCom3` = supervisor? Hoje as regiões apontam para `RH20xx` ou para PN de pessoa? | `SAPService.get_all_regions()` (GET) | V3 confirmada ou revista **antes** da F3 |
| 3 | Lista completa de equipes e gerências (o print começa na linha 4; faltam 3) e se `RH2040 – EQUIPE RJ 1` (Ativo = Não) entra | GET `BusinessPartners` com `startswith(CardCode,'RH')` | catálogo estático da F3 |
| 4 | `PAOLAK` em `Users`: `InternalKey`, `UserCode`, `UserName`, `eMail` (vazio?) e o padrão de código de 5 internos recentes | GET `Users?$filter=…` | confirma a regra de código da F1 |
| 5 | E-mails dos 8 gerentes internos | Graph `GET /users?$filter=startswith(displayName,…)` | tabela da Seção 0 completa |
| 6 | Grupos de Gerência no Exchange/M365 | Graph `GET /groups?$search="displayName:geren"` | IDs para `resolve_groups_by_job_title` (F3) |
| 7 | UBD: formato de `leaders` no `POST /users` (e-mail? id?); `PATCH /workspace/v1/users/{id}` aceita `leaders`? Como ler os líderes atuais? | documentação Skore + GET de um usuário existente (sem PATCH) | viabilidade da F5 |

**DoD:** as 7 perguntas respondidas aqui. Uma resposta que contradiga V3 ou inviabilize a F5
volta para o gestor antes da fase dependente.

### Respostas (2026-09-25)

**#1** — A tabela `regioes` (Supabase do outro projeto) tem `regiao` como chave (PK, texto,
ex. `"084-UBERLANDIA"`). E-mail do representante em `email_vendedor`; e-mail do supervisor em
`email_supervisor`; e-mail do gerente em `email_gerente` (nomes livres em `supervisor`/
`gerente`). Não existe `created_at`; existem `atualizado_em` (timestamp por linha, varia) e
`sincronizado_em` (carimbo do lote de sincronização do SAP — igual nas 5 linhas da amostra, ou
seja roda em lote). O intervalo típico entre atualizações não dá para fixar com uma amostra de
5 linhas — recomendo observar `sincronizado_em` por alguns dias antes de travar
`AUTOMACAO_SYNC_ATRASO_MIN` (chute inicial conservador: 30–60 min). ⚠️ **Risco de acesso:** a
`SUPABASE_ANON_KEY` devolveu 401/lista vazia (RLS bloqueia leitura anônima); só a
`SUPABASE_KEY` (chave de serviço, com poder de escrita) conseguiu ler. A F4 deve pedir ao dono
do projeto `regioes` uma role/visão só de leitura antes de ir para produção — hoje a única
credencial que funciona pode escrever. · fonte: `f0_regioes.py` (GET/SELECT via PostgREST),
retry com `SUPABASE_KEY`.

**#2** — 140 regiões. `U_IB_CodCom2` está **vazio em 100% das 140 regiões** (não guarda
gerente hoje). `U_IB_CodCom1` mistura `RH2020` ("REGIAO DIRETA", marcador, 45×) com códigos
`F######`/`FF#####` (PN de representante individual, fora do padrão `RH`). `U_IB_CodCom3` só
tem códigos `RH20xx` (marcadores de equipe/gerência, nenhum ainda substituído por PN de pessoa
na amostra vista — ex. `RH2064` "VENDA DIRETA" 26×, `RH2087`, `RH2052`). ⚠️ **Contradiz V3:** o
mapeamento "`CodCom2` = gerente, `CodCom3` = supervisor" não se sustenta — `CodCom2` não é
usado em nenhuma das 140 regiões; o gerente não aparece em campo nenhum do `IB_CO_REGIAO`
nesta consulta. A relação de gerência hoje só existe na tabela `regioes` (Supabase), como
texto (`gerente`/`email_gerente`) — não no SAP. **Decisão do gestor necessária antes da F3:**
(a) existe um 4º campo ou relação hierárquica no SAP que guarde o gerente e não foi
encontrado, ou (b) a F3 assume que o SAP só guarda representante (`CodCom1`) e supervisor
(`CodCom3`), e o gerente é gravado/lido só no `regioes`/UBD. · fonte:
`SAPService.get_all_regions()` (GET).

**#3** — Lista completa dos PNs `RH*` obtida via GET paginado (`BusinessPartners`,
`startswith(CardCode,'RH')`). Contém equipes (`EQUIPE <UF> <N>`, ex. `RH2029 EQUIPE RS 1`),
duas gerências nomeadas explicitamente (`RH2005 GERENTE SP`, `RH2033 GERENTE MG/RJ`),
marcadores especiais (`RH2020 REGIAO DIRETA`, `RH2064 VENDA DIRETA`, `RH2090 EQUIPE DIRETA
SP`) e dezenas de PNs de pessoa física (supervisores/gerentes já substituídos, com
`Valid`/`Frozen` indicando ativo/inativo). `RH2040 – EQUIPE RJ 1` está na lista com
`Valid: tNO / Frozen: tYES` (inativo), confirmando a suspeita do gestor. Lista completa (140+
itens) ficou no log do script, não reproduzida aqui por tamanho. · fonte: GET
`BusinessPartners?$filter=startswith(CardCode,'RH')&$select=CardCode,CardName,CardForeignName,Valid,Frozen`
(paginado).

**#4** — `PAOLAK`: `InternalKey=439`, `UserName="PAOLA KEMEL"`, `eMail=null` (confirma a causa
raiz do bug: usuário sem e-mail cadastrado), `Locked="tNO"`. Padrão de código nos 15 usuários
internos mais recentes (`InternalKey` 433–449): **não é uma regra única** — a maioria usa
primeiro nome em maiúsculas sem pontuação (`JULIANA`, `THAIS`, `MAICOM`, `DIOGENES`, `JOSE`,
`UINE`), mas os 3 mais recentes (`InternalKey` 447–449) usam minúsculas, alguns com ponto para
nome composto (`rafael`, `test_intern`, `integracao.ploomes`), e há um código curto (`LAB3`).
Relevante para a F1 (que assume `PRIMEIRONOME + inicial`): a base real tem convenção mista, o
gerador de código da F1 deve tolerar variação, não assumir um padrão fixo. · fonte: GET
`Users?$filter=UserCode eq 'PAOLAK'&$select=...` e `Users?$select=...&$orderby=InternalKey
desc&$top=15`.

**#5** — 7 de 8 gerentes encontrados sem ambiguidade: Alessandro Lodion →
`alessandro@bondmann.com.br`; Anderson Viana → `anderson@bondmann.com.br`; Guilherme Rosa →
`guilherme.rosa@bondmann.com.br`; Mariana Silva → `mariana.silva@bondmann.com.br`; Patricia
Alves → `patricia.alves@bondmann.com.br`; Thiago Rodrigues →
`thiago.rodrigues@bondmann.com.br`; Rogério Rossini → `rogerio@bondmann.com.br` (cadastrado no
Graph como "Rogerio Rossini", sem acento). Todos os 7 com `accountEnabled: true`. "Elias
Kiesten" **não foi encontrado**; uma busca por "Elias" isolado retornou **"Elias Kirsten"**
(`elias@bondmann.com.br`) — provável erro de grafia no nome da Seção 0 ("Kiesten" →
"Kirsten"). **(a confirmar pelo gestor)** antes de preencher a tabela de gerências de internos
da Seção 0. · fonte: Graph `GET /users?$search="displayName:<nome>"` (GET).

**#6** — Candidatos a grupo de "Gerência": `Gerência`
(`0dc77a73-b07a-451f-8497-4e70d93192a3`, M365 Group), `Gerência - Acompanhamento Semanal`
(`401e4848-225d-4b5f-8f9b-692ebff76ca6`, M365 Group), `Gerencia`
(`9bc94d0a-8dca-4f3e-86fe-44cbb7692ee9`, `bd.gerencia@bondmann.com.br`, M365 Group) —
`Gerenciamento de Projetos wmw` é de projetos, não de liderança, e foi descartado. Candidatos
a "Supervisão": `BD Supervisão` (`c868acc4-f8fc-449c-b996-71abc68f4b32`, sem `groupTypes` →
não é M365 Group, provável grupo de segurança) e `Supervisão`
(`dc770076-e3c6-4554-8fd0-9e62168542e4`, `supervisao@bondmann.com.br`, M365 Group). Nenhum se
destaca como "o" grupo oficial — **(a confirmar pelo gestor)** qual usar em
`resolve_groups_by_job_title` (F3). · fonte: Graph
`GET /groups?$search="displayName:<termo>"` (GET), termos `geren`/`Geren`/`supervis`.

**#7** — A API do Learning.rocks (Skore) **não devolve** lista de líderes no GET:
`workspace/v1/users/{id}` e `workspace/v2/users/{id}` retornam só o booleano `is_leader` (se a
própria pessoa lidera outros), nunca quem lidera essa pessoa. `workspace/v2/users/{id}`
responde 404 (não existe — só a v1 tem "user details"). A coleção Postman do projeto
(referenciada por `tests/parse_skore_postman_doc.py`) documenta `leaders` como **lista de IDs
numéricos** (ex. `"leaders": [40]`), tanto em `POST /workspace/v1/users` (criação) quanto em
`PATCH /workspace/v1/users/:id` (atualização de usuário existente) — **não** e-mails. Ou seja:
dá para **escrever** líderes de um usuário já existente via `PATCH` (não executado, conforme a
regra de só-leitura desta fase), mas não dá para **ler de volta** os líderes atuais pela API —
a F5 precisa manter o próprio estado; o desenho de "espelho com proteção" da V4 já assume
isso, não é um bloqueio novo. Testado com 3 pessoas reais da amostra da `regioes`
(`neander.faria@bondmann.com.br`, representante sem liderança; `andre.mandelli@bondmann.com.br`
e `marcelo.neves@bondmann.com.br`, ambos `is_leader: true`) — mesmo resultado nos três. ·
fonte: `LearningRocksService.find_user_by_email` + GET `v1/users/{id}` e `v2/users/{id}`
(GET), doc Postman local (sem `PATCH`).

### F0b — Levantamento complementar (2026-09-25, tarde; somente leitura)

**#2 revisado** — Todos os campos de `IB_CO_REGIAO`: `U_IB_CodCom1..5`, `U_IB_CodCom1_Anterior`,
`U_IB_UF`, `U_IB_assessor`, `U_IB_codigo`, `U_IB_codigo_WMW`. `U_IB_CodCom4` tem só 4 valores e é
o **gerente**: `RH2004` BRUNO TIARA DA SILVA (`bruno.tiara@`, 54 regiões), `RH2016` ANDRE LUIZ
MANDELLI (`andre.mandelli@`, 40), `RH2030` WALLYSSON … MEDEIROS (`wallysson.medeiros@`, 20) e
`RH2064` VENDA DIRETA (25, marcador). `U_IB_CodCom3` é o **supervisor**: PN da pessoa (ex.:
`RH2052` JORGE AURELIO CUNHA VAN LARE, com e-mail no PN) ou o marcador `EQUIPE …` quando a vaga
está aberta (`RH2021`, `RH2074`, `RH2090`, `RH2024`, `RH2039`, `RH2082`, `RH2083`, `RH2084`,
`RH2048`). Cada supervisor aparece sempre com o mesmo gerente (pares `CodCom3 → CodCom4`
consistentes). PNs de pessoa têm `EmailAddress` preenchido — é por ele que a F3 acha o PN. ·
fonte: GET `IB_CO_REGIAO` + `BusinessPartners?$filter=CardCode eq '…'`.

**#1 revisado** — Chave anon e publishable fornecidas pelo gestor: HTTP 200 e **0 linhas** nas
duas (RLS da `regioes` não libera SELECT para `anon`). Ver V12. · fonte: GET PostgREST
`/rest/v1/regioes`.

**#7 — consequência para a F5** — a API não devolve os líderes atuais e o `PATCH` recebe lista
de IDs. Se o `PATCH` **substituir** a lista, a proteção de líderes manuais (V4) não é possível
só pela API. A F5 começa por um spike com usuário de teste (F5.0) antes de qualquer código.

---

## Seção 2 — F1: SAP, usuário interno (bug Paola)

**Repositório:** worker.

### Problema

Desligamento de Paola Kemel (2026-09-24): etapa *SAP Business One (Service Layer) — Bloqueio e
Liberação de Licenças* ❌ com `SAP respondeu 400 ao consultar Users('paola.kemel'): code 201
"Invalid query option: key value is not matched with its type"`, embora o usuário exista.

### Causa raiz

1. Na Service Layer, a chave da entidade `Users` é o **`InternalKey` (inteiro)**, não o
   `UserCode`. `Users('paola.kemel')` sempre dá 400 (`services/sap_api.py::find_internal_user_code`).
2. O fallback por e-mail só roda em 404; o 400 sobe como erro e aborta o fluxo.
3. Mesmo com a chave certa, a busca não acharia: no SAP o usuário é `UserCode = PAOLAK`,
   `UserName = PAOLA KEMEL`, **e-mail vazio**, e a automação procura pelo prefixo do e-mail.
4. Mesmo defeito em `create_internal_user`: a checagem `GET Users('{code}')` falha, a exceção
   é engolida (`existing = False`) e o fluxo tenta um `POST` duplicado. O `PATCH Users('{code}')`
   do bloqueio também usaria a chave errada.
5. Código de usuário na criação: a factory usa o primeiro nome em minúsculas (`paola`); o
   padrão real é `PRIMEIRONOME + inicial do último sobrenome` em maiúsculas (`PAOLAK`).

### Design

**`SAPService.find_internal_user(full_name, email, user_code=None) -> dict | None`**
(substitui `find_internal_user_code`). Sempre por coleção com `$filter`, nunca chave na URL;
`$select=InternalKey,UserCode,UserName,eMail,Locked`. Ordem, parando no primeiro achado único:

1. `UserCode eq '<código derivado>'` (e o `user_code` explícito, se vier);
2. `tolower(eMail) eq '<email>'`;
3. `UserName eq '<NOME EM MAIÚSCULAS>'`; se nada, tenta sem acentos.

Mais de um resultado num passo ⇒ `SAPUsuarioAmbiguo` (FAILED com a lista
`UserCode – UserName`). Nenhum ⇒ `None`. Escape de `'` → `''`. HTTP ≠ 200 ⇒ `RuntimeError`
com código e texto.

**Bloqueio:** `PATCH Users(<InternalKey>)` com `{"Locked": "tYES"}`; licenças liberadas pelo
`UserCode` encontrado. Não encontrado ⇒ etapa ✅ "nada a bloquear" (regra de 2026-09-15).
Ambíguo ⇒ FAILED.

**Criação:**
- Código: `derivar_user_code(full_name)` = primeiro nome + 1ª letra do último sobrenome,
  maiúsculas, sem acentos (`Paola Kemel` → `PAOLAK`). A factory passa a usar essa função.
- Já existe com o mesmo `UserName` ⇒ `PATCH Users(<InternalKey>)` (reaproveita e desbloqueia).
- Código em uso por **outra** pessoa ⇒ próxima letra do sobrenome (`PAOLAKE`, …); esgotou ⇒
  FAILED "defina o código manualmente".
- `POST /Users` só quando não existe. O resultado traz sempre `internal_key` e `user_code`.

Sem mudança de contrato.

### Ajustes da revisão final (2026-09-25)

- **W1** — Reaproveitar usuário existente (`action == "atualizado"`) só faz `PATCH` de
  `UserName/eMail/Superuser/Locked`; a senha antiga continua valendo. O worker não manda mais
  `senha_sap` ao RH nesse caso — a etapa traz a nota "senha SAP existente mantida — redefinir
  manualmente".
- **W2** — `tolower(eMail)` nunca tinha sido validado contra o SAP real; a sondagem confirmou
  400 ("Query string error - Not supported function"). `find_internal_user` agora, só nesse
  caso, tenta de novo com `eMail eq '<minúsculo>'` sem cast.
- **W3** — Parte dos códigos SAP antigos segue outra convenção (F0 #4): só o primeiro nome
  (`JULIANA`, `THAIS`), sem a inicial do sobrenome. A busca por código agora também tenta o
  primeiro nome sozinho, validado pelo `UserName` como os demais; "quase-acertos" (código bateu,
  nome não) são citados na mensagem quando o desligamento não acha ninguém.
- **Pendência:** o gestor ainda não decidiu se um "quase-acerto" (código de outra pessoa) deve
  fazer a etapa de desligamento **FAILAR** em vez de manter "nada a bloquear" — por ora a regra
  de 2026-09-15 continua valendo sem mudança.

---

## Seção 3 — F2: Urgência e desligamento às 17h

**Repositório:** portal.

- **Motor de formulários:** `campos_dinamicos.py` só tem `checkbox_multi` (`TIPOS_VALIDOS`,
  linha 29). Adicionar o tipo `checkbox` simples (valor `true`; ausente = não marcado) no
  motor e no partial `_campos_dinamicos.html`, sem mudar o Químico.
- **Formulário de Desligamento:** `CampoDef("urgente", "Urgência — executar assim que o TI
  aprovar", "checkbox")`, opcional, visível para qualquer perfil. Ajuda: "Ignora a data e o
  horário do desligamento; os acessos são bloqueados no momento da aprovação do TI."
- **Agendamento (`calcular_executar_apos`):** `hora_desligamento` default **17**;
  `DESLIGAMENTO` com `urgente` ⇒ `now()`. `REVOGAR_LICENCA` continua às 07h de
  `data_desligamento + 15 dias`, com ou sem urgência.
- **Payload:** campo `urgente: bool` (aditivo; o worker ignora).
- **Card:** selo **"Urgente — roda ao aprovar"** no resumo e no confirm do botão; sem
  urgência, mostra o horário agendado (17h).
- **Detalhe do chamado:** "Urgência: Sim" pela rotulagem existente.

### Desvios da revisão final (2026-09-25)

- O motor de formulários grava o `checkbox` marcado como a string `VALOR_CHECKBOX_MARCADO`
  ("Sim"), não `true`; a conversão para `bool` acontece ao montar o payload do job
  (`app/domain/automacao.py`, `"urgente": str(dados.get("urgente") or "") == VALOR_CHECKBOX_MARCADO`).
  O contrato do job (`payload["urgente"]`) continua `bool`, como documentado.
- **Item em aberto:** o card só mostra "Execução liberada a partir de …" depois que a TI aprova
  e o job nasce `NA_FILA` (`app/templates/workspace/atendimento.html`, `{% if job.status ==
  'NA_FILA' %}`). Antes da aprovação não há job — o horário padrão de 17h (sem urgência) não é
  exibido de antemão no card, só depois de aprovado.

---

## Seção 4 — F3: Supervisor × Gerente e vagas na `IB_CO_REGIAO`

**Repositórios:** portal + worker. **Depende de:** F0 #2, #3, #6.

Corrige de passagem o bug atual: desligar supervisor grava `RH2020` no campo Supervisor de
**uma** região, apagando o vínculo da equipe (o processo manual manda voltar o código da
equipe).

### Catálogo estático (portal)

`app/domain/vagas_comerciais.py` com `(codigo, nome)` extraídos na F0 #3. Base conhecida:

- **Equipes (supervisor):** RH2018 EQUIPE SP 1 · RH2019 EQUIPE SP 2 · RH2021 EQUIPE MG 2 ·
  RH2024 EQUIPE SP 5 · RH2029 EQUIPE RS 1 · RH2032 EQUIPE PR 2 · RH2039 EQUIPE SP 6 ·
  RH2040 EQUIPE RJ 1 (inativa no SAP; F0 decide) · RH2041 EQUIPE PR 1 · RH2043 EQUIPE MG 1 ·
  RH2047 EQUIPE SP 3 · RH2048 EQUIPE MG 3 · RH2053 EQUIPE SC 1 · RH2054 EQUIPE PR 4 ·
  RH2055 EQUIPE GO1 · RH2074 EQUIPE PR3 · RH2075 EQUIPE RS 2 · RH2082 EQUIPE SC 2 ·
  RH2083 EQUIPE RS 5 · RH2084 EQUIPE PR 5 · RH2086 EQUIPE SP 4 · RH2090 EQUIPE DIRETA SP ·
  (+ 3 linhas não visíveis no print).
- **Gerências (gerente):** RH2005 GERENTE SP · RH2033 GERENTE MG/RJ.

Rótulo no select: `"RH2018 — EQUIPE SP 1"`. Equipe nova = PR de uma linha.

### Portal — formulário (Criação e Desligamento)

| Campo | Mudança |
|---|---|
| `perfil` | + opção **"Gerente"**; `perfil_curto` → "Gerente" |
| `regiao_wmw` / `regiao` | visível **só para Representante** |
| `equipe` (novo) | select obrigatório, visível se Supervisor, rótulo **"Equipe correspondente"** |
| `gerencia` (novo) | select obrigatório, visível se Gerente, rótulo **"Gerência correspondente"** |

Validação servidor-side contra o catálogo. Título automático:
`"Criação de usuário — Nome (Supervisor · EQUIPE SP 1)"`.

### Worker

- **Modelos/factories:** `UserProfileType.GERENTE`; `_PERFIL["GERENTE"]`; cargo fixo
  "Gerente"; setor fixo "Gerentes de vendas (sem fila)". Campos `vacancy_code`,
  `vacancy_name`, `vacancy_kind`. `regiao` deixa de ser obrigatória para SUPERVISOR; `vaga`
  obrigatória para SUPERVISOR/GERENTE (`PayloadInvalido`).
- **M365:** supervisor inalterado (Grupo Bondmann, Representantes ×2, Supervisão, BD
  Supervisão); gerente = Grupo Bondmann + grupos de Gerência (F0 #6).
- **UBD (times):** gerente = Bondmann + Lideranças.
- **SAP — `find_regions_by_field(campo, valor)`:** `IB_CO_REGIAO?$filter=<campo> eq '<valor>'`
  com paginação (fallback: `get_all_regions` + filtro local).
- **SAP — `occupy_vacancy(kind, vacancy_code, email, full_name)`:**
  1. PN da pessoa via `find_business_partner`; não achou ⇒ `RuntimeError` "PN de <nome> não
     encontrado no SAP — cadastre o PN e reexecute só esta etapa".
  2. `campo = U_IB_CodCom3` (EQUIPE) | `U_IB_CodCom4` (GERENCIA) — V3 revisada (F0b).
  3. Regiões com `campo == vacancy_code`. **Zero** ⇒ `RuntimeError` listando quem ocupa hoje
     as regiões da vaga (sinal de que o anterior não foi desligado).
  4. `PATCH IB_CO_REGIAO('<Code>')` com `{campo: CardCode}` em cada região
     (`update_commission_region` ganha `gerente=` → `U_IB_CodCom4`). Falha parcial ⇒
     `RuntimeError` com as já alteradas; na reexecução, regiões que já têm o CardCode contam
     como feitas.
  5. Retorna `{card_code, campo, regioes}`.
- **SAP — `release_vacancy(...)`:** regiões com `campo == CardCode` ⇒ volta `vacancy_code`.
  Zero regiões ou PN não encontrado ⇒ ✅ "nada a devolver".
- **`flow.py`:**
  - Criação de SUPERVISOR/GERENTE: nova etapa
    `"SAP Business One (Service Layer) - Ocupar Vaga (Equipe/Gerência)"`.
  - Desligamento de SUPERVISOR/GERENTE: nova etapa
    `"SAP Business One (Service Layer) - Devolver Vaga (Equipe/Gerência)"` **no lugar** da
    etapa `RH2020`, que fica só para REPRESENTANTE.

---

## Seção 5 — F4: Liderança na UBD ao criar usuário

**Repositórios:** portal + worker. **Depende de:** F0 #1, #5, #7 e F3.

| Perfil | Líderes na UBD | Fonte |
|---|---|---|
| Representante | supervisor + gerente da região | `regioes` |
| Supervisor | gerente(s) das regiões da equipe | `regioes` |
| Gerente | nenhum | — |
| Interno | gerente da "Gerência responsável"; "Outra" ⇒ `gestor_email` | tabela da Seção 0 |

Corrige também o bug atual: `gestor_email` chega ao worker como `manager_email` e **nunca** é
enviado à UBD (`flow.py::_ubd` não passa `leaders`), nem quando o usuário já existe.

### Portal

- **Leitura da `regioes` (`app/integracoes/regioes.py`):** PostgREST com a chave anon (V12):
  envs `REGIOES_API_URL` + `REGIOES_API_KEY`, só GET.
  Sem env ⇒ recurso desligado (líderes "não resolvidos"). Funções:
  `lideres_da_regiao(codigo)`, `lideres_da_equipe(codigo_vaga)`, `mapa_lideranca()` (F5).
  Colunas definidas pela F0 #1. **Teste de guarda:** o módulo não contém
  `INSERT|UPDATE|DELETE|UPSERT|ALTER|CREATE|DROP|TRUNCATE` nem HTTP diferente de GET (V1).
- **Formulário:** `gerencia_interna` (select obrigatório, visível se Interno, 8 gerências +
  "Outra"); `gestor_email` obrigatório quando "Outra". Mapa em
  `app/domain/gerencias_internas.py`.
- **Resolução no clique do TI:** `resolver_lideres(payload)` grava `payload.lideres_ubd`. Se a
  `regioes` estiver indisponível ⇒ `lideres_ubd = []` + `lideres_nao_resolvidos = true`; o TI
  pode aprovar mesmo assim (a F5 corrige depois).
- **Card:** "Líderes na UBD: fulano@, beltrano@" ou "líderes não resolvidos — a sincronização
  diária aplica".

### Worker

- Factory: `lideres_ubd` → `UserCreationData.ubd_leaders`; payload v1 com só `gestor_email`
  continua funcionando (`[manager_email]`).
- `_ubd`: usuário novo ⇒ `create_user(..., leaders=…)`; usuário existente ⇒
  `LearningRocksService.set_leaders(user_id, leaders)`. Líder inexistente na UBD ⇒ etapa
  **SUCCESS** com aviso ("líder X não encontrado — aplicar manualmente"); não aborta.
- Resultado traz `leaders_aplicados` e `avisos`, exibidos na nota interna.

---

## Seção 6 — F5: Sincronização de liderança (24 h)

**Repositórios:** portal + worker. **Depende de:** F4 e F0 #7.

Mantém a liderança de **representantes e supervisores** na UBD igual à `regioes`. Para cada
usuário: adiciona os líderes esperados que faltam e remove **só** os que estão em
`lideres_gerenciados` e não são os esperados (V4).

### Dados — migration `0092_automacao_sync_lideranca.sql`

```sql
ALTER TYPE automacao_tipo ADD VALUE IF NOT EXISTS 'SINCRONIZAR_LIDERANCA';
-- ADD VALUE em migration/transação própria, como exige o Postgres
ALTER TABLE automacao_jobs ALTER COLUMN chamado_id DROP NOT NULL;
ALTER TABLE automacao_jobs ADD CONSTRAINT ck_automacao_jobs_chamado
  CHECK (tipo = 'SINCRONIZAR_LIDERANCA' OR chamado_id IS NOT NULL);
CREATE UNIQUE INDEX ux_automacao_jobs_sync_ativo ON automacao_jobs (tipo)
  WHERE tipo = 'SINCRONIZAR_LIDERANCA' AND status IN ('NA_FILA','EXECUTANDO');
```

RLS: jobs sem chamado são visíveis só para ADMIN (a política atual filtra pelo setor do
chamado; revisar para `chamado_id IS NULL`). Escrita continua só via `admin_connection()`.
Seção 5 do plano v1 atualizada **antes** do código.

### Portal

- **Gatilho diário:** o `_loop_vigilancia` enfileira um job às **06h** (Brasília) se não
  houver um ativo nem um concluído no dia.
- **Gatilho por evento:** ao processar o resultado de CRIACAO/DESLIGAMENTO de SUPERVISOR ou
  GERENTE com a etapa de vaga em SUCCESS ⇒ sync com
  `executar_apos = now() + AUTOMACAO_SYNC_ATRASO_MIN` (atraso medido na F0 #1) e
  `origem_chamado_id`.
- **Controles:** `AUTOMACAO_TIPOS` precisa conter `SINCRONIZAR_LIDERANCA`;
  `AUTOMACAO_SYNC_MODO = relatorio | aplicar` (default **relatorio**).
- **Resultado:** grava em `resultado`; com `origem_chamado_id`, posta nota interna com o
  relatório; `nao_encontrados` ou `erros` ⇒ e-mail a `AUTOMACAO_ALERTA_EMAIL`. `regioes`
  indisponível ⇒ job não é criado; alerta na 2ª falha consecutiva.

### Worker

- `LearningRocksService.get_leaders(user_id)` / `set_leaders(user_id, emails)` (F0 #7).
- `run_leadership_sync_flow`: etapa única `"UBD Learning.rocks - Sincronização de Liderança"`.
  Para cada usuário: acha na UBD por e-mail (inexistente/inativo ⇒ `nao_encontrados`); calcula
  `novo = (atuais − (lideres_gerenciados − esperados)) ∪ esperados`; se mudou e
  `modo == aplicar` ⇒ `set_leaders`. Erro num usuário não para os demais; heartbeat a cada N
  usuários.
- Resultado: `{verificados, corrigidos: [{email, adicionados, removidos}], nao_encontrados, erros}`.

---

## Seção 7 — Contrato v2 (resumo)

Atualizar `docs/automacao_api.md` na F3 (mudanças da F2 são aditivas e não exigem versão).

| Mudança | Fase | Tipo |
|---|---|---|
| `urgente: bool` no DESLIGAMENTO | F2 | aditivo (v1) |
| `perfil` aceita `GERENTE` | F3 | v2 |
| `vaga: {tipo: EQUIPE\|GERENCIA, codigo, nome}` para supervisor e gerente; `regiao` nula para eles | F3 | v2 |
| Etapas novas: Ocupar Vaga, Devolver Vaga | F3 | v2 |
| `lideres_ubd: [email]`, `lideres_nao_resolvidos` na CRIACAO | F4 | v2 |
| Tipo `SINCRONIZAR_LIDERANCA` (`modo`, `origem_chamado_id`, `usuarios`, `lideres_gerenciados`) + etapa de sync | F5 | v2 |

**Troca de versão sem janela quebrada:** (1) o worker passa a aceitar `versao_contrato` `1` e
`2`; (2) o portal passa a emitir `2` e `/saude` devolve `"2"`.

---

## Seção 8 — Testes, homologação e protocolo de atualização

### Testes automatizados

| Fase | Worker | Portal |
|---|---|---|
| F1 | `tests/test_sap_usuarios.py` (Service Layer falsa): regressão do 400 em `Users('x')`; caso Paola (e-mail vazio → `PAOLAK` → `Users(<int>)`); ambíguo; não encontrado; colisão `PAOLAKE`; mesma pessoa ⇒ PATCH; apóstrofo; `test_worker.py` verde | — |
| F2 | — | `calcular_executar_apos` (17h, urgente ⇒ agora, data passada ⇒ agora, criação 07h); checkbox persiste e aparece rotulado; job com `urgente`; selo no card |
| F3 | factory GERENTE/SUPERVISOR com e sem `vaga`; ocupar (feliz, PN ausente, vaga sem regiões, falha parcial + reexecução); devolver (feliz, nada a devolver); supervisor não passa pelo `RH2020`; aceita contrato 1 e 2 | condicionais do formulário; código fora do catálogo recusado; payload v2; título automático |
| F4 | `_ubd` envia `leaders`; `set_leaders` em usuário existente; líder inexistente vira aviso; payload v1 com `gestor_email` | `resolver_lideres` nos 4 perfis; "Outra" exige `gestor_email`; fail-soft sem env; **guarda de somente-leitura** |
| F5 | espelho com proteção (adiciona, remove supervisor antigo, preserva manual, sem mudança ⇒ sem PATCH); `relatorio` nunca faz PATCH; erro isolado | agendamento diário idempotente; disparo por evento com atraso; migration 0092; nota no chamado de origem; alerta; `tests/e2e -m rls` |

### Homologação (DoD para marcar 🟢 Pronto)

| Fase | Roteiro |
|---|---|
| F0 | 7 perguntas respondidas na Seção 1; e-mails dos gerentes confirmados pelo gestor |
| F1 | Dry-run pelo card ⇒ reexecução **real** só da etapa SAP do chamado da Paola ⇒ ✅ e `PAOLAK` com "Bloqueado" marcado no SAP |
| F2 | Dry-run com data futura + urgência ⇒ worker pega na hora; sem urgência ⇒ `executar_apos` = 17h da data |
| F3 | GET da `IB_CO_REGIAO` guardado ⇒ dry-run de supervisor de teste (regiões certas no relatório) ⇒ ocupar e devolver vaga com PN de teste ⇒ `IB_CO_REGIAO` final idêntica à original |
| F4 | Dry-run (líderes do card batem com a `regioes`) ⇒ criação real de representante de teste ⇒ liderança conferida na UBD ⇒ desligamento do usuário de teste |
| F5 | Modo relatório em produção ⇒ **gestor revisa** o relatório ⇒ `AUTOMACAO_SYNC_MODO=aplicar` ⇒ 3 representantes conferidos na UBD ⇒ desligar supervisor de teste e ver o sync por evento removê-lo e manter o gerente |

### Protocolo de atualização

A cada PR desta frente:

1. Atualizar a **Matriz de fases** (estado e data) e o **Status** do cabeçalho.
2. Respostas da F0 viram texto fixo na Seção 1 (e na Seção 0, se mudarem uma decisão).
3. Se DDL, RLS ou contrato mudarem: Seção 5 do plano v1 e `docs/automacao_api.md` **antes** do
   código.
4. `docs/CHANGELOG.md`; no worker, `PLANO_MESTRE_AUTOMACAO.md` e
   `docs/runbook_automacao_worker.md`.

# Spec de Design — Automação de Acessos: Execução Automática e Alerta de Erro TI

- **Data:** 2026-10-06
- **Status:** Aprovado
- **Autor/Gestor:** Osvaldo Bello
- **Frente:** Automação de Criação e Desligamento de Usuários (Portal de Chamados)
- **Documento Relacionado:** [`plano_md_mestre_automacao_acessos.md`](../../plano_md_mestre_automacao_acessos.md)

---

## 1. Contexto e Motivação

Originalmente, a automação de criação e desligamento de acessos no Portal de Chamados exigia um gate humano obrigatório (Decisão D2 do plano mestre): o operador de TI precisava abrir o chamado no Workspace e clicar em "Executar automação" para que o job fosse enfileirado para o worker.

Com a maturidade e a estabilização das integrações nos sistemas de destino (Microsoft 365, UBD Learning.rocks, SAP Business One Service Layer, WMW Vendas Web e CompanySIP), o processo deve passar a rodar **automaticamente** assim que o chamado entrar no portal, eliminando a dependência do aval manual prévio do operador.

**Requisito Crítico:**
Caso ocorra qualquer erro durante o processamento da automação:
1. O fluxo deve ser interrompido imediatamente.
2. O chamado não pode ser resolvido automaticamente (permanece em atendimento com pendências ou falha para tratamento da equipe de TI).
3. Um e-mail de alerta deve ser enviado obrigatoriamente para `ti@bondmann.com.br` contendo os dados do chamado, em qual processo/etapa a falha ocorreu e a descrição detalhada do erro e logs retornados.

---

## 2. Arquitetura e Fluxo de Dados

### 2.1 Enfileiramento na Abertura do Chamado (`app/routes/portal.py`)
No endpoint `criar_chamado`, após a inserção e persistência do chamado no banco (`commit_now()`):
1. Detecta se a subcategoria selecionada é de automação de acessos (`dom.tipo_da_subcategoria(nome_subcategoria_val)`), abrangendo:
   - `Criação de Novo Usuário` (`CRIACAO`)
   - `Desligamento / Bloqueio de Acesso` (`DESLIGAMENTO`)
2. Verifica se existem respostas estruturadas no formulário (`dados_formulario_val`).
3. Dispara `automacao_svc.enfileirar_automatico(...)` de forma segura (dentro de `try/except` com log), garantindo que nenhuma inconsistência da automação trave ou impeça a abertura do chamado pelo usuário.

### 2.2 Camada de Orquestração (`app/services/automacao.py`)
Implementação de `enfileirar_automatico(chamado: dict[str, Any], settings: Settings | None = None) -> dict[str, Any] | None`:
1. **Validação de Kill Switch e Tipos Liberados:**
   - Confere se `settings.automacao_ativa` é verdadeiro e se o tipo (`CRIACAO` ou `DESLIGAMENTO`) consta em `settings.automacao_tipos`. Caso contrário, registra log e encerra sem erro.
2. **Definição do Ator Responsável (`aprovado_por`):**
   - Resolve um perfil da TI para constar no registro do job (ex.: conta de serviço `ti@bondmann.com.br` ou o primeiro operador/admin de TI ativo via consulta administrativa no banco).
   - Fallback: se nenhum perfil de TI for localizado, associa ao `cliente_id` do chamado.
3. **Cálculo do Agendamento (`executar_apos`):**
   - Respeita o calendário e regras de negócio de `dom.calcular_executar_apos(...)`:
     - **Criação:** Agendado para as 07h (Brasília) do dia útil anterior à `data_inicio` informada (considerando a tabela de feriados). Se não houver data informada, ou for hoje/passada, o agendamento é imediato (`agora`).
     - **Desligamento:** Agendado para as 17h (Brasília) da `data_desligamento` informada. Se o formulário tiver a opção `urgente` marcada, ou a data for hoje/passada, o agendamento é imediato (`agora`).
4. **Persistência do Job:**
   - Utiliza `repo_admin.admin_agendar_job(...)` via conexão administrativa (`admin_connection`), evitando barreiras de RLS para solicitantes que não pertençam ao setor de TI (ex.: analistas do RH).
   - O job nasce com status `NA_FILA`.
   - Registra evento `AUTOMACAO_ENFILEIRADA` em `historico_chamados`.

---

## 3. Tratamento de Erros e Envio de E-mail de Alerta

### 3.1 Interrupção do Processo
- No worker, a primeira etapa com falha (`FAILED`) interrompe o fluxo imediatamente, marcando as etapas subsequentes como não executadas.
- No portal, em `processar_resultado`:
  - Se qualquer etapa retornar `FAILED` ou houver `erro_geral`, o job é classificado como `FALHOU` (se nada tiver sido concluído) ou `CONCLUIDO_COM_PENDENCIAS` (se etapas anteriores concluíram com sucesso).
  - O chamado **NUNCA** é marcado como `RESOLVIDO` quando há falhas/pendências.
  - Nenhuma mensagem pública de encerramento total é postada para o cliente. Apenas mensagem parcial (se aplicável) e nota técnica interna para a TI.

### 3.2 Notificação por E-mail para `ti@bondmann.com.br`
- A função `destinatarios_alerta(settings: Settings) -> list[str]` passa a ter `ti@bondmann.com.br` como destinatário padrão e garantido:
  - Adiciona `ti@bondmann.com.br`.
  - Combina com quaisquer outros endereços definidos em `settings.automacao_alerta_email`.
  - Normaliza para minúsculas e remove duplicatas.
- O e-mail de alerta (`dom.texto_email_alerta_ti`) detalha:
  - Código e título do chamado.
  - Dados do colaborador (nome, e-mail, perfil, setor/cargo).
  - Status final da execução.
  - **Identificação exata de em qual processo/etapa ocorreu a falha**:
    - Nome do step com indicativo visual de erro (`FAILED`).
    - Descrição da falha (`error_message` / `erro`).
    - Logs e detalhes técnicos retornados pelo sistema (`details`).
  - O que ficou pendente e não pôde ser executado.
  - Link direto para atendimento do chamado no portal.
- Na rotina de vigilância (`vigiar_uma_vez`), caso um worker trave no meio da execução (sem heartbeat) e o job seja marcado como `FALHOU`, o alerta também é despachado por e-mail para `destinatarios_alerta`.

---

## 4. Resiliência de Criação de Contas no Portal

Em `_criar_conta_portal(payload, aprovado_por)`:
- A atribuição de papel (`role`) e setor (`departamento_id`) em `perfis` passa a ser executada via conexão administrativa (`admin_connection`), prevenindo violações de RLS da política `perfis_admin_all` quando o job for originado automaticamente sem um JWT de TI ativo no contexto.

---

## 5. Interface do Usuário (Workspace / Atendimento)

- No card de Automação de Acessos em `/workspace/chamados/{id}`:
  - Informa visualmente que o job foi enfileirado automaticamente na abertura do chamado.
  - Exibe a data/hora planejada de execução (`executar_apos`).
  - Mantém para o operador de TI a opção de **Reexecutar pendências** ou **Executar simulação (dry-run)** caso o job tenha falhado ou concluído com pendências.

---

## 6. Estratégia de Testes

1. **Testes Unitários de Domínio e Orquestração (`tests/test_automacao.py`):**
   - Enfileiramento automático de chamado de `CRIACAO` com data futura (verifica cálculo de dia útil às 07h).
   - Enfileiramento de `DESLIGAMENTO` padrão (às 17h) e urgente (imediato).
   - Enfileiramento sem data ou com data passada (imediato).
   - Validação de que chamado sem subcategoria ou sem dados estruturados não enfileira job.
   - Validação de kill switch (`automacao_ativa = False` não enfileira).
   - Verificação de `destinatarios_alerta` contendo sempre `ti@bondmann.com.br`.
   - Simulação de etapa `FAILED` verificando parada, não resolução do chamado e envio do e-mail de alerta com logs.
2. **Testes de Integração da Rota do Portal (`tests/test_portal.py`):**
   - Criação de chamado de Criação e Desligamento com campos dinâmicos preenchidos, conferindo a chamada a `enfileirar_automatico`.

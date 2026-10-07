# Especificação de Design — Validação e Registro de Causa de Atraso em Demandas de Marketing

- **Data:** 2026-10-07
- **Setor:** Marketing
- **Contexto:** Portal de Chamados Bondmann Química

---

## 1. Visão Geral e Objetivos

No setor de Marketing, o fluxo de trabalho é baseado em demandas com prazos acordados via `data_entrega` (no mínimo 48h úteis, com vencimento às 18:00 no horário de Brasília) ou marcadas explicitamente como `sem_prazo`.

O objetivo desta especificação é implementar o controle e registro obrigatório da causa de atraso quando uma demanda ultrapassa a data de entrega estipulada, impedindo que tarefas atrasadas sejam finalizadas sem que o motivo e eventuais especificações sejam registrados.

### Critérios de Aceite:
1. **Identificação de Atraso:** Uma demanda de Marketing é considerada atrasada quando possui prazo (`limite_resolucao IS NOT NULL`) e o momento atual ultrapassa esse limite (`agora > limite_resolucao`).
2. **Obrigatoriedade na Conclusão:** É obrigatório preencher a causa do atraso para transicionar uma demanda atrasada para o status `RESOLVIDO`.
3. **Causas Padronizadas:** A lista de opções válidas é restrita a:
   - `SEM CAUSA REGISTRADA`
   - `AGUARDANDO DEFINIÇÃO INTERNA`
   - `DEPENDÊNCIA DE EXECUÇÃO INTERNA`
   - `DEPENDÊNCIA DE TERCEIROS`
4. **Campo de Especificação:** Disponibilizar uma caixa de texto para que o operador possa adicionar especificações/detalhes adicionais sobre o atraso.
5. **Comportamento no Kanban:** O arraste de um card atrasado para a coluna "Concluídos" sem causa registrada é bloqueado, emitindo alerta e instruindo o operador a abrir o chamado para preencher a causa.
6. **Comportamento na Tela de Atendimento:** O formulário de encerramento (`/encerrar`) apresenta a seleção da causa e a caixa de especificação diretamente na área de conclusão se a demanda estiver atrasada.
7. **Atualização nos Relatórios e Dashboard:** O Dashboard de Marketing e os relatórios exportáveis passam a refletir os novos rótulos de causas e a especificação.

---

## 2. Arquitetura e Modelo de Dados

### 2.1 Banco de Dados (Migration `0095_marketing_causa_especificacao_atraso.sql`)

1. **Alteração na tabela `chamados`**:
   ```sql
   ALTER TABLE chamados ADD COLUMN IF NOT EXISTS especificacao_atraso text;
   ```

2. **Segurança e Imutabilidade (Trigger `enforce_cliente_so_avaliacao`)**:
   - As colunas `causa_atraso` e `especificacao_atraso` não devem ser editáveis por solicitantes com papel `CLIENTE`.
   - Adicionar `NEW.causa_atraso` e `NEW.especificacao_atraso` na tupla comparativa de integridade (`v_outras_colunas_mudaram`).

3. **Normalização dos Dados Legados**:
   - Atualizar registros existentes com valores antigos em minúsculas/títulos para a padronização oficial:
     ```sql
     UPDATE chamados SET causa_atraso = 'SEM CAUSA REGISTRADA' WHERE causa_atraso ILIKE '%Sem causa%';
     UPDATE chamados SET causa_atraso = 'AGUARDANDO DEFINIÇÃO INTERNA' WHERE causa_atraso ILIKE '%Aguardando definição%';
     UPDATE chamados SET causa_atraso = 'DEPENDÊNCIA DE EXECUÇÃO INTERNA' WHERE causa_atraso ILIKE '%Dependência de execução%';
     ```

---

## 3. Lógica de Domínio e Backend

### 3.1 Módulo de Domínio (`app/domain/marketing.py`)

Criar funções puras e isoladas para regras do setor de Marketing:
- `CAUSAS_ATRASO_MARKETING`: tupla imutável com as 4 opções oficiais.
- `demanda_marketing_atrasada(chamado: dict, agora: datetime | None = None) -> bool`:
  - Retorna `True` se `departamento == "Marketing"`, `limite_resolucao` não for nulo e `agora > limite_resolucao`.
  - Retorna `False` se `sem_prazo` for verdadeiro ou `limite_resolucao` for nulo, ou se `agora <= limite_resolucao`.
- `validar_conclusao_marketing(chamado: dict, causa_atraso: str | None, agora: datetime | None = None) -> str | None`:
  - Retorna mensagem de erro se a demanda estiver atrasada e nenhuma causa válida for selecionada.
  - Retorna `None` se a validação passar.

### 3.2 Repositório (`app/repositories/atendimento.py` & `chamados.py`)

- `salvar_marketing_meta`: expandir assinatura para receber `especificacao_atraso: str | None`.
- `obter`: incluir `c.especificacao_atraso` no `SELECT` principal do chamado.
- Atualizar queries do `AdminRepo` em `app/repositories/admin.py` para incluir `c.especificacao_atraso` em `atraso_rows`.

### 3.3 Rotas do Workspace (`app/routes/workspace.py`)

1. **Rota de Encerramento (`POST /workspace/chamados/{chamado_id}/encerrar`)**:
   - Captura `causa_atraso: str = Form("")` e `especificacao_atraso: str = Form("")`.
   - Consulta o chamado e verifica se há bloqueio via `validar_conclusao_marketing`.
   - Se houver pendência: renderiza a tela com mensagem de erro amigável (`marketing_bloqueio_erro`).
   - Se válido e fornecido: persiste `causa_atraso` e `especificacao_atraso` antes de executar `alterar_status("RESOLVIDO")`.

2. **Rota de Troca de Status (`POST /workspace/chamados/{chamado_id}/status`)**:
   - Quando `novo_status == "RESOLVIDO"`:
     - Verifica se a demanda é de Marketing e está atrasada sem `causa_atraso` gravada.
     - Se chamada via drag do Kanban (`X-Kanban-Drag`):
       ```json
       {
         "ok": false,
         "erro": "Demanda em atraso: para concluí-la, é obrigatório preencher a causa do atraso na tela de atendimento."
       }
       ```
     - Se chamada via form tradicional de ações: renderiza o atendimento com o alerta de bloqueio.

3. **Rota de Metadados (`POST /workspace/chamados/{chamado_id}/marketing-meta`)**:
   - Atualiza `volume`, `origem_demanda`, `causa_atraso` e `especificacao_atraso`.

---

## 4. Frontend e Interface do Usuário

### 4.1 Tela de Atendimento (`app/templates/workspace/atendimento.html`)

1. **Bloco de Encerramento (`/encerrar`)**:
   - Quando `demanda_atrasada` for `True`:
     - Exibe aviso visual (estilo âmbar): "⚠️ Esta demanda está atrasada. Selecione a causa do atraso para poder concluir."
     - Exibe `<select name="causa_atraso" required>` com as 4 opções.
     - Exibe `<input type="text" name="especificacao_atraso">` com placeholder descritivo.
2. **Card "Informações do Dashboard"**:
   - Atualiza opções do select de causa para as 4 constantes em caixa alta.
   - Adiciona input para `especificacao_atraso`.

### 4.2 Dashboard & Relatórios (`app/static/js/admin_marketing.js` e exports)

1. **Constantes do Gráfico de Causas**:
   - `const causaLabels = ["SEM CAUSA REGISTRADA", "AGUARDANDO DEFINIÇÃO INTERNA", "DEPENDÊNCIA DE EXECUÇÃO INTERNA", "DEPENDÊNCIA DE TERCEIROS"];`
2. **Tabela de Demandas Atrasadas**:
   - Exibir a causa e, se houver especificação, formatar com subtítulo/itálico legível.
3. **Exportador XLSX (`app/services/export_marketing.py`)**:
   - Incluir a especificação na planilha de controle de atrasos.

---

## 5. Estratégia de Testes

- **Testes Unitários de Domínio (`tests/test_marketing_atraso.py`)**:
  - Testar detecção de atraso com datas no passado, futuro e casos com `sem_prazo`.
  - Testar validação de causas obrigatórias com valores válidos, vazios e inválidos.
- **Testes de Integração de Rotas (`tests/test_workspace.py`)**:
  - Testar bloqueio no Kanban drag ao tentar mover card atrasado para `RESOLVIDO` sem causa.
  - Testar permissão no Kanban drag quando a causa já estiver registrada.
  - Testar bloqueio e sucesso na rota `/encerrar` com e sem envio de `causa_atraso` e `especificacao_atraso`.
  - Testar persistência via rota `/marketing-meta`.

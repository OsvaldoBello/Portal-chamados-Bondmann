# Plano de Implementação — Validação e Registro de Causa de Atraso em Demandas de Marketing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar obrigatório o preenchimento da causa e especificação de atraso para finalizar demandas do setor de Marketing que ultrapassarem a Data de Entrega acordada (`limite_resolucao`), com suporte no Kanban, na tela de atendimento e nos relatórios de gestão.

**Architecture:** Módulo de domínio puro `app/domain/marketing.py` para detecção de atraso e validação de conclusão; coluna `especificacao_atraso` em `chamados` (migration `0095`) protegida por trigger RLS contra manipulação de clientes; gates de validação em `/workspace/chamados/{id}/status` e `/workspace/chamados/{id}/encerrar`; integração visual em `atendimento.html`, Kanban e Dashboard de Marketing (`admin_marketing.js`).

**Tech Stack:** Python 3.12+, FastAPI, asyncpg / Supabase PostgreSQL, Jinja2, TailwindCSS, Chart.js, pytest.

## Global Constraints
- Causas válidas (exatamente estas 4): `SEM CAUSA REGISTRADA`, `AGUARDANDO DEFINIÇÃO INTERNA`, `DEPENDÊNCIA DE EXECUÇÃO INTERNA`, `DEPENDÊNCIA DE TERCEIROS`.
- Uma demanda de Marketing só é considerada atrasada quando possui prazo (`limite_resolucao IS NOT NULL`) e `agora > limite_resolucao`. Chamados com `sem_prazo = True` nunca atrasam.
- Clientes com perfil `CLIENTE` não podem alterar `causa_atraso` nem `especificacao_atraso` (imposto pelo trigger `enforce_cliente_so_avaliacao`).
- No Kanban drag-and-drop, tentar mover um card atrasado para Concluídos sem causa registrada deve retornar `{"ok": false, "erro": "..."}` exibindo alerta nativo e desfazendo o movimento.

---

### Task 1: Banco de Dados — Migration 0095 e Proteção de Integridade

**Files:**
- Create: `supabase/migrations/0095_marketing_causa_especificacao_atraso.sql`
- Modify: `plano_mestre_desenvolvimento.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Produces: Coluna `especificacao_atraso` na tabela `chamados`; atualização da função de trigger `enforce_cliente_so_avaliacao()`.

- [ ] **Step 1: Criar arquivo da migration 0095**

Criar `supabase/migrations/0095_marketing_causa_especificacao_atraso.sql` contendo:
- `ALTER TABLE chamados ADD COLUMN IF NOT EXISTS especificacao_atraso text;`
- Normalização de valores legados de `causa_atraso` para uppercase oficial.
- Atualização da função `enforce_cliente_so_avaliacao()` incluindo `NEW.causa_atraso` e `NEW.especificacao_atraso` em `v_outras_colunas_mudaram`.

- [ ] **Step 2: Verificar sintaxe da migration**

Executar verificação com o script ou testador de migrations do projeto.

- [ ] **Step 3: Documentar migration no plano mestre e changelog**

Registrar a migration `0095` em `plano_mestre_desenvolvimento.md` e em `docs/CHANGELOG.md`.

- [ ] **Step 4: Commit**

```bash
git add supabase/migrations/0095_marketing_causa_especificacao_atraso.sql plano_mestre_desenvolvimento.md docs/CHANGELOG.md
git commit -m "feat(marketing): adicionar migration 0095 de especificacao de atraso e protecao de integridade"
```

---

### Task 2: Domínio de Negócio — Módulo `app/domain/marketing.py` com TDD

**Files:**
- Create: `app/domain/marketing.py`
- Test: `tests/test_marketing_atraso.py`

**Interfaces:**
- Produces:
  - `CAUSAS_ATRASO_MARKETING: tuple[str, ...]`
  - `demanda_marketing_atrasada(chamado: dict, agora: datetime | None = None) -> bool`
  - `validar_conclusao_marketing(chamado: dict, causa_atraso: str | None = None, agora: datetime | None = None) -> str | None`

- [ ] **Step 1: Escrever os testes unitários com falha**

Criar `tests/test_marketing_atraso.py` testando:
- Chamado no prazo (`agora <= limite_resolucao`) $\rightarrow$ não atrasado.
- Chamado atrasado (`agora > limite_resolucao`) $\rightarrow$ atrasado.
- Chamado com `sem_prazo = True` $\rightarrow$ não atrasado.
- Chamado de outro setor (ex: TI) com `agora > limite_resolucao` $\rightarrow$ `validar_conclusao_marketing` retorna `None` (não bloqueia).
- Chamado de Marketing atrasado sem `causa_atraso` $\rightarrow$ retorna mensagem de erro de bloqueio.
- Chamado de Marketing atrasado com `causa_atraso` válida $\rightarrow$ retorna `None` (validação passa).
- Chamado de Marketing atrasado com `causa_atraso` inválida (fora das 4 opções) $\rightarrow$ retorna mensagem de erro.

- [ ] **Step 2: Executar testes para confirmar falha**

Run: `.venv\Scripts\python.exe -m pytest tests/test_marketing_atraso.py -v`
Expected: FAIL (módulo ou funções não encontradas).

- [ ] **Step 3: Implementar `app/domain/marketing.py`**

Implementar as constantes e funções puras em `app/domain/marketing.py`:
- `CAUSAS_ATRASO_MARKETING = ("SEM CAUSA REGISTRADA", "AGUARDANDO DEFINIÇÃO INTERNA", "DEPENDÊNCIA DE EXECUÇÃO INTERNA", "DEPENDÊNCIA DE TERCEIROS")`
- `demanda_marketing_atrasada` garantindo manipulação segura de fuso horário UTC / timezone-aware.
- `validar_conclusao_marketing` verificando a causa informada ou prévia do chamado.

- [ ] **Step 4: Executar testes para confirmar aprovação**

Run: `.venv\Scripts\python.exe -m pytest tests/test_marketing_atraso.py -v`
Expected: PASS (todos os testes verdes).

- [ ] **Step 5: Commit**

```bash
git add app/domain/marketing.py tests/test_marketing_atraso.py
git commit -m "feat(marketing): implementar regras de dominio para deteccao e validacao de atraso"
```

---

### Task 3: Camada de Repositório — Suporte a `especificacao_atraso`

**Files:**
- Modify: `app/repositories/atendimento.py:41-80, 860-885`
- Modify: `app/repositories/chamados.py:510-530`
- Modify: `app/repositories/admin.py:825-837, 1031-1040`

**Interfaces:**
- Consumes: `especificacao_atraso` na tabela `chamados`.
- Produces: Métodos `obter`, `salvar_marketing_meta` e queries de admin expondo `especificacao_atraso`.

- [ ] **Step 1: Atualizar `AtendimentoRepo.obter`**

Incluir `c.especificacao_atraso` no `SELECT` de chamados em `app/repositories/atendimento.py`.

- [ ] **Step 2: Atualizar `AtendimentoRepo.salvar_marketing_meta` e fachada `ChamadosRepo`**

Adicionar parâmetro `especificacao_atraso: str | None = None` no método `salvar_marketing_meta`, atualizando a query `UPDATE chamados SET ..., especificacao_atraso = $5`.

- [ ] **Step 3: Atualizar queries de atraso em `AdminRepo`**

Em `app/repositories/admin.py`, selecionar `c.especificacao_atraso` na query de `atraso_rows` e incluir `"especificacao": r.get("especificacao_atraso") or ""` em `atrasos_data`.

- [ ] **Step 4: Rodar suíte de testes de workspace e repositório**

Run: `.venv\Scripts\python.exe -m pytest tests/test_atendimento_service.py tests/test_admin.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/atendimento.py app/repositories/chamados.py app/repositories/admin.py
git commit -m "feat(repositories): suportar especificacao_atraso em chamados e painel admin"
```

---

### Task 4: Rotas do Workspace — Gates de Encerramento e Mudança de Status

**Files:**
- Modify: `app/routes/workspace.py:705-778, 1100-1126`
- Modify: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `app.domain.marketing.validar_conclusao_marketing`, `demanda_marketing_atrasada`, `CAUSAS_ATRASO_MARKETING`.
- Produces: Bloqueio de encerramento sem causa; suporte a `causa_atraso` e `especificacao_atraso` em `/encerrar` e `/status`.

- [ ] **Step 1: Escrever testes de integração em `tests/test_workspace.py`**

Adicionar testes em `tests/test_workspace.py`:
- `test_drag_kanban_bloqueia_marketing_atrasado_sem_causa`: simular `POST /workspace/chamados/{id}/status` com `X-Kanban-Drag: 1` em chamado atrasado sem causa, esperando `{"ok": False, "erro": "..."}`.
- `test_drag_kanban_permite_marketing_atrasado_com_causa`: simular o mesmo com causa preenchida, esperando `{"ok": True}`.
- `test_encerrar_bloqueia_marketing_atrasado_sem_causa`: chamado de Marketing atrasado sem causa tenta `/encerrar`, esperando que o chamado não seja resolvido e exiba erro.
- `test_encerrar_sucesso_com_causa_e_especificacao`: chamado de Marketing atrasado envia `/encerrar` com `causa_atraso` e `especificacao_atraso`, persistindo e marcando como `RESOLVIDO`.

- [ ] **Step 2: Executar testes para confirmar falha**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workspace.py -k "marketing_atrasado" -v`
Expected: FAIL.

- [ ] **Step 3: Implementar validação nas rotas em `app/routes/workspace.py`**

- Na rota `mudar_status`:
  - Se `novo_status == "RESOLVIDO"`: verificar `validar_conclusao_marketing`.
  - Se houver bloqueio e `X-Kanban-Drag`: retornar `JSONResponse({"ok": False, "erro": ...})`.
  - Se houver bloqueio via form: retornar `_carregar_atendimento` com `marketing_bloqueio_erro`.
- Na rota `encerrar`:
  - Receber `causa_atraso: str = Form("")` e `especificacao_atraso: str = Form("")`.
  - Se houver bloqueio por validação de marketing: retornar `_carregar_atendimento` com `marketing_bloqueio_erro`.
  - Se válido e preenchido: persistir metadados antes de transicionar status.
- Na rota `salvar_marketing_meta`:
  - Receber e persistir `especificacao_atraso`.
- Em `_carregar_atendimento`:
  - Passar `demanda_atrasada`, `causas_atraso_marketing` e `marketing_bloqueio_erro` para o template `atendimento.html`.

- [ ] **Step 4: Executar testes de integração para confirmar aprovação**

Run: `.venv\Scripts\python.exe -m pytest tests/test_workspace.py -k "marketing_atrasado" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/workspace.py tests/test_workspace.py
git commit -m "feat(workspace): aplicar gates de validacao para conclusao de chamados de marketing atrasados"
```

---

### Task 5: Frontend — Atualização da Tela de Atendimento, Dashboard e Relatórios

**Files:**
- Modify: `app/templates/workspace/atendimento.html:390-425, 590-625`
- Modify: `app/static/js/admin_marketing.js:20-30, 405-420, 505-525`
- Modify: `app/templates/admin/relatorio_marketing_export.html`
- Modify: `app/services/export_marketing.py:170-190`

**Interfaces:**
- Consumes: Variáveis de contexto de template e APIs de dados de marketing.
- Produces: Interface amigável com seleção de causa e especificação de atraso.

- [ ] **Step 1: Atualizar template `app/templates/workspace/atendimento.html`**

- No bloco "Concluir chamado" (`/encerrar`):
  - Se `demanda_atrasada` for `True` e setor for Marketing:
    - Adicionar card de alerta informando a obrigatoriedade da causa.
    - Adicionar `<select name="causa_atraso">` com as 4 opções oficiais.
    - Adicionar `<input type="text" name="especificacao_atraso">` com rótulo "Especificação do Atraso".
- No card "Informações do Dashboard":
  - Atualizar as opções de `causa_atraso` para as 4 constantes oficiais.
  - Adicionar o campo `especificacao_atraso`.
  - Ajustar rótulo para remover o antigo `(se > 5 dias)`.

- [ ] **Step 2: Atualizar `app/static/js/admin_marketing.js` e `relatorio_marketing_export.html`**

- Definir `causaLabels` com os 4 rótulos em maiúsculas:
  `["SEM CAUSA REGISTRADA", "AGUARDANDO DEFINIÇÃO INTERNA", "DEPENDÊNCIA DE EXECUÇÃO INTERNA", "DEPENDÊNCIA DE TERCEIROS"]`.
- Na tabela de atrasos (`buildAtrasosTable`), exibir a causa e, se houver especificação, formatar com destaque visual.

- [ ] **Step 3: Atualizar exportador XLSX `app/services/export_marketing.py`**

- Adicionar a coluna "Especificação" na aba "Tempo e Atrasos".

- [ ] **Step 4: Executar suíte de testes de exportação e front**

Run: `.venv\Scripts\python.exe -m pytest tests/test_export_marketing.py tests/test_export_html.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/templates/workspace/atendimento.html app/static/js/admin_marketing.js app/templates/admin/relatorio_marketing_export.html app/services/export_marketing.py
git commit -m "feat(ui): adicionar campos de causa e especificacao no atendimento e dashboard de marketing"
```

---

### Task 6: Validação Final e Suíte de Testes Completa

**Files:**
- Run: Suíte completa de testes unitários e de integração.

- [ ] **Step 1: Executar a suíte de testes completa**

Run: `.venv\Scripts\python.exe -m pytest tests/ -v`
Expected: Todos os testes passando sem erros.

- [ ] **Step 2: Verificar integridade do git status e git diff**

Run: `git status`
Expected: Working tree clean.

- [ ] **Step 3: Atualizar documento vivo de contexto (plano mestre)**

Registrar a conclusão da feature na tabela de implementação e na Seção 5.2 do `plano_mestre_desenvolvimento.md`.

- [ ] **Step 4: Commit final**

```bash
git add plano_mestre_desenvolvimento.md
git commit -m "docs(mestre): registrar conclusao da obrigatoriedade de causa de atraso no marketing"
```

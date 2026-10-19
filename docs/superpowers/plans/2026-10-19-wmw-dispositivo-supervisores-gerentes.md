# Plano de Implementação: Dispositivo WMW para Supervisores e Gerentes Comerciais

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Habilitar e exigir a seleção do dispositivo do app WMW (iOS, Android, Simulador) no formulário de Criação de Novo Usuário para Supervisores e Gerentes Comerciais, sincronizando com a integração v1.5.0 da automação.

**Architecture:** Atualizar a definição do campo `dispositivo_wmw` em `app/domain/formularios_acessos.py` para usar visibilidade condicional baseada na tupla de perfis comerciais (`PERFIL_REPRESENTANTE`, `PERFIL_SUPERVISOR`, `PERFIL_GERENTE`), atualizar o motor de testes e atualizar o plano mestre de acessos `plano_md_mestre_automacao_acessos_v2.md`.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, Pytest.

## Global Constraints
- Manter compatibilidade com `montar_payload` e os enums do contrato de automação (`IOS`, `ANDROID`, `SIMULADOR`).
- Manter `regiao_wmw` visível e obrigatória exclusivamente para `PERFIL_REPRESENTANTE`.
- Preservar `CAMPOS_DESLIGAMENTO` inalterado.
- 100% de aprovação na suíte de testes de formulários e automação (`test_formularios_dinamicos.py` e `test_automacao.py`).

---

### Task 1: Testes de Validação para Dispositivo WMW em Supervisores e Gerentes (TDD Red)

**Files:**
- Modify: `tests/test_formularios_dinamicos.py`
- Modify: `tests/test_automacao.py`

**Interfaces:**
- Consumes: `app.domain.formularios_acessos`, `app.domain.campos_dinamicos.validar_campos`, `app.domain.automacao.montar_payload`
- Produces: Testes automatizados que garantem que `dispositivo_wmw` é exigido para Supervisor e Gerente na criação.

- [ ] **Step 1: Atualizar testes existentes e adicionar novos testes de validação em `tests/test_formularios_dinamicos.py`**

Adicionar fixture com `dispositivo_wmw` em `test_criacao_supervisor_pede_equipe_e_nao_regiao` e `test_criacao_gerente_pede_gerencia`.
Adicionar novo teste `test_criacao_supervisor_e_gerente_exigem_dispositivo_wmw`:
```python
def test_criacao_supervisor_e_gerente_exigem_dispositivo_wmw():
    # Supervisor sem dispositivo_wmw falha
    dados_sup_sem_disp = _criacao_representante(
        perfil=[ac.PERFIL_SUPERVISOR], regiao_wmw=[""], dispositivo_wmw=[""],
        equipe=["RH2018 — EQUIPE SP 1"],
    )
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), dados_sup_sem_disp)
    assert not ok and "Dispositivo do app WMW" in erro

    # Gerente sem dispositivo_wmw falha
    dados_ger_sem_disp = _criacao_representante(
        perfil=[ac.PERFIL_GERENTE], regiao_wmw=[""], dispositivo_wmw=[""],
        gerencia=["RH2005 — GERENTE SP"],
    )
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), dados_ger_sem_disp)
    assert not ok and "Dispositivo do app WMW" in erro

    # Interno não exige nem grava dispositivo_wmw
    ok, erro, limpo = validar_campos(ac.campos_criacao(SETORES), _criacao_interno())
    assert ok, erro
    assert "dispositivo_wmw" not in limpo
```

- [ ] **Step 2: Adicionar asserção em `tests/test_automacao.py` garantindo extração de `dispositivo_wmw` no payload de supervisor e gerente**

Em `tests/test_automacao.py`, verificar que `montar_payload` para criação de supervisor com `dispositivo_wmw="IOS (iPhone / iPad)"` gera `"dispositivo_wmw": "IOS"`.

- [ ] **Step 3: Executar os testes e verificar que falham no novo teste (Red)**

Run: `pytest tests/test_formularios_dinamicos.py -k "test_criacao_supervisor_e_gerente_exigem_dispositivo_wmw"`
Expected: FAIL com `assert not ok and "Dispositivo do app WMW" in erro` (pois atualmente `dispositivo_wmw` não é visível para Supervisor/Gerente).

- [ ] **Step 4: Commit dos testes**

```bash
git add tests/test_formularios_dinamicos.py tests/test_automacao.py
git commit -m "test(acessos): adicionar testes para dispositivo WMW em supervisores e gerentes"
```

---

### Task 2: Implementação da Visibilidade Condicional em `formularios_acessos.py` (TDD Green)

**Files:**
- Modify: `app/domain/formularios_acessos.py:117-195`

**Interfaces:**
- Consumes: `PERFIL_REPRESENTANTE`, `PERFIL_SUPERVISOR`, `PERFIL_GERENTE`, `CampoDef`
- Produces: `_SO_COMERCIAIS_WMW` e `campos_criacao` com `dispositivo_wmw` visível e obrigatório para os 3 perfis comerciais.

- [ ] **Step 1: Modificar `app/domain/formularios_acessos.py`**

Adicionar a tupla de perfis comerciais com WMW:
```python
_SO_COMERCIAIS_WMW = ("perfil", (PERFIL_REPRESENTANTE, PERFIL_SUPERVISOR, PERFIL_GERENTE))
```

Atualizar o campo `dispositivo_wmw` em `campos_criacao`:
```python
        CampoDef(
            "dispositivo_wmw", "Dispositivo do app WMW", "select", obrigatorio=True,
            opcoes=_DISPOSITIVOS_WMW, visivel_se=_SO_COMERCIAIS_WMW,
        ),
```

- [ ] **Step 2: Executar testes de validação dinâmica**

Run: `pytest tests/test_formularios_dinamicos.py -v`
Expected: 100% PASS

- [ ] **Step 3: Executar testes de automação**

Run: `pytest tests/test_automacao.py -v`
Expected: 100% PASS

- [ ] **Step 4: Commit**

```bash
git add app/domain/formularios_acessos.py
git commit -m "feat(acessos): habilitar selecao de dispositivo WMW para supervisores e gerentes"
```

---

### Task 3: Atualização do Plano Mestre e Changelog

**Files:**
- Modify: `plano_md_mestre_automacao_acessos_v2.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Documentação mestre do projeto atualizada refletindo a integração v1.5.0 do WMW para liderança.

- [ ] **Step 1: Atualizar `plano_md_mestre_automacao_acessos_v2.md`**

1. Atualizar a premissa V9 (tabela de decisões arquiteturais):
```markdown
| **V9** | WMW para supervisor e gerente (integrado na v1.5.0 da automação e refletido no portal): geração automática de usuário por PN SAP, seleção de dispositivo no portal (IOS/Android/Simulador) e credenciais de acesso repassadas ao RH. |
```
2. Na tabela de formulários da Seção 4 ("Portal — formulário (Criação e Desligamento)"), atualizar:
```markdown
| `dispositivo_wmw` | visível para **Representante, Supervisor e Gerente** |
```

- [ ] **Step 2: Atualizar `docs/CHANGELOG.md`**

Registrar a alteração no formulário de criação de supervisores e gerentes.

- [ ] **Step 3: Commit**

```bash
git add plano_md_mestre_automacao_acessos_v2.md docs/CHANGELOG.md
git commit -m "docs(acessos): documentar dispositivo WMW para supervisores e gerentes no plano mestre"
```

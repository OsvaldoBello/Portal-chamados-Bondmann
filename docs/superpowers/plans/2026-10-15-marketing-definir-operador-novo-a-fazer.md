# Plano de Implementação — Atribuição de Operador em Chamados "Novo" e "A Fazer" no Marketing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o staff do setor de Marketing defina o operador responsável em chamados nos status "Novo" e "A fazer" sem a obrigatoriedade de iniciar o atendimento, preservando o operador já pré-definido quando o atendimento for iniciado formalmente.

**Architecture:** Regras de permissão no serviço puro `AtendimentoService` (`pode_atribuir` e `pode_iniciar`); preservação de operador com `COALESCE(operador_id, $2::uuid)` em `AtendimentoRepo.iniciar_atendimento`; injeção das flags no contexto de rota em `app/routes/workspace.py`; e atualização de visibilidade condicional em `app/templates/workspace/atendimento.html`.

**Tech Stack:** Python 3.14 / 3.12+, FastAPI, asyncpg / PostgreSQL, Jinja2, pytest.

## Global Constraints
- No Marketing, o campo "Operador responsável" deve estar disponível para o staff do setor mesmo quando o chamado estiver em `NOVO` ou `A_FAZER` e sem operador assumido (`operador_id IS NULL`).
- Ao clicar em "Iniciar atendimento" (ou arrastar o card no Kanban), se o chamado já tiver um operador pré-definido, ele DEVE ser mantido (não sobrescrever com o usuário logado). Se não tiver operador, quem iniciou assume.
- No Marketing, o botão "Iniciar atendimento" deve permanecer visível e disponível enquanto o chamado estiver em `NOVO` ou `A_FAZER`, mesmo se já houver operador pré-definido.
- O composer de chat/notas internas deve ser liberado assim que o operador for definido, mesmo em `NOVO` ou `A_FAZER`.
- Os demais departamentos (TI, RH, Manutenção, etc.) mantêm 100% o comportamento atual inalterado.
- Seguir TDD com testes unitários em `tests/test_atendimento_service.py` e testes de rota em `tests/test_workspace.py`.

---

### Task 1: Serviço de Atendimento — Regras de Permissão em `AtendimentoService` (TDD)

**Files:**
- Modify: `app/services/atendimento.py`
- Test: `tests/test_atendimento_service.py`

**Interfaces:**
- Produces: Novos campos `pode_atribuir: bool` e `pode_iniciar: bool` no dataclass `PermissoesAtendimento` retornado por `AtendimentoService.permissoes(chamado, perfil, user_id)`.

- [ ] **Step 1: Escrever testes unitários para as novas permissões em `tests/test_atendimento_service.py`**

Adicionar os testes que cobrem os cenários do Marketing e dos demais setores:

```python
def test_marketing_pode_atribuir_em_novo_e_a_fazer_sem_operador():
    chamado = _chamado(
        departamento_id=DEP_MKT,
        cliente_id=USER_AUTOR,
        operador_id=None,
        autoatendimento=True,
    )
    chamado["departamento"] = "Marketing"
    chamado["status"] = "NOVO"
    perm = AtendimentoService.permissoes(chamado, _perfil(departamento_id=DEP_MKT), USER_STAFF)
    assert perm.pode_atribuir is True
    assert perm.pode_iniciar is True
    assert perm.pode_atender is False


def test_marketing_pode_iniciar_mesmo_com_operador_definido_em_novo_ou_a_fazer():
    chamado = _chamado(
        departamento_id=DEP_MKT,
        cliente_id=USER_AUTOR,
        operador_id=USER_STAFF,
        autoatendimento=True,
    )
    chamado["departamento"] = "Marketing"
    chamado["status"] = "A_FAZER"
    perm = AtendimentoService.permissoes(chamado, _perfil(departamento_id=DEP_MKT), USER_STAFF)
    assert perm.pode_atribuir is True
    assert perm.pode_iniciar is True
    assert perm.pode_atender is True


def test_marketing_pode_iniciar_false_quando_em_atendimento():
    chamado = _chamado(
        departamento_id=DEP_MKT,
        cliente_id=USER_AUTOR,
        operador_id=USER_STAFF,
        autoatendimento=True,
    )
    chamado["departamento"] = "Marketing"
    chamado["status"] = "EM_ATENDIMENTO"
    perm = AtendimentoService.permissoes(chamado, _perfil(departamento_id=DEP_MKT), USER_STAFF)
    assert perm.pode_atribuir is True
    assert perm.pode_iniciar is False
    assert perm.pode_atender is True


def test_demais_setores_mantem_pode_atribuir_apenas_quando_ja_assumido():
    chamado = _chamado(
        departamento_id=DEP_TI,
        cliente_id=USER_AUTOR,
        operador_id=None,
        autoatendimento=False,
    )
    chamado["departamento"] = "TI"
    chamado["status"] = "NOVO"
    perm = AtendimentoService.permissoes(chamado, _perfil(departamento_id=DEP_TI), USER_STAFF)
    assert perm.pode_atribuir is False
    assert perm.pode_iniciar is True  # equivale a pode_reivindicar
    assert perm.pode_atender is False
```

- [ ] **Step 2: Executar teste para verificar que falha**

Run: `pytest tests/test_atendimento_service.py -v`
Expected: FAIL com AttributeError ou TypeError (`pode_atribuir` e `pode_iniciar` não existem).

- [ ] **Step 3: Implementar as novas regras em `app/services/atendimento.py`**

Modificar `PermissoesAtendimento` e o método `permissoes`:

```python
@dataclass(frozen=True)
class PermissoesAtendimento:
    dept_bate: bool
    eh_autor: bool
    eh_autoatendimento: bool
    bloqueado_por_autoria: bool
    pode_reivindicar: bool
    pode_atender: bool
    pode_atribuir: bool
    pode_iniciar: bool
```

E no cálculo de permissões:
```python
        eh_autor = str(chamado.get("cliente_id") or "") == str(user_id)
        eh_autoatendimento = bool(chamado.get("autoatendimento"))
        dept_bate = cls.dept_bate(chamado, perfil)
        ja_assumido = chamado.get("operador_id") is not None
        bloqueado_por_autoria = eh_autor and not eh_autoatendimento
        eh_marketing = (chamado.get("departamento") or "").strip().lower() == "marketing"

        pode_reivindicar = dept_bate and not bloqueado_por_autoria and not ja_assumido
        pode_atender = dept_bate and not bloqueado_por_autoria and ja_assumido

        if eh_marketing:
            pode_atribuir = dept_bate and not bloqueado_por_autoria
            pode_iniciar = dept_bate and not bloqueado_por_autoria and (chamado.get("status") in ("NOVO", "A_FAZER"))
        else:
            pode_atribuir = pode_atender
            pode_iniciar = pode_reivindicar

        return PermissoesAtendimento(
            dept_bate=dept_bate,
            eh_autor=eh_autor,
            eh_autoatendimento=eh_autoatendimento,
            bloqueado_por_autoria=bloqueado_por_autoria,
            pode_reivindicar=pode_reivindicar,
            pode_atender=pode_atender,
            pode_atribuir=pode_atribuir,
            pode_iniciar=pode_iniciar,
        )
```

- [ ] **Step 4: Executar testes para verificar que passam**

Run: `pytest tests/test_atendimento_service.py -v`
Expected: Todos os testes PASS (12 passed).

- [ ] **Step 5: Commit da Task 1**

```bash
git add app/services/atendimento.py tests/test_atendimento_service.py
git commit -m "feat(atendimento): adicionar permissoes pode_atribuir e pode_iniciar para marketing"
```

---

### Task 2: Repositório de Atendimento — Preservação de Operador em `iniciar_atendimento`

**Files:**
- Modify: `app/repositories/atendimento.py:343-365`

**Interfaces:**
- Consumes: `operador_id` em chamados.
- Produces: `iniciar_atendimento` preserva `operador_id` pré-existente via `COALESCE(operador_id, $2::uuid)` e grava o operador efetivo no log de histórico.

- [ ] **Step 1: Ajustar query SQL e registro de auditoria em `app/repositories/atendimento.py`**

Em `AtendimentoRepo.iniciar_atendimento`:

```python
            row = await conn.fetchrow(
                """
                UPDATE chamados
                   SET status = $4::status_chamado,
                       operador_id = COALESCE(operador_id, $2::uuid)
                 WHERE id = $1::uuid
                   AND status IN ('NOVO'::status_chamado, 'A_FAZER'::status_chamado)
                   AND ($3::boolean OR cliente_id <> $2::uuid)
             RETURNING id, status, operador_id
                """,
                chamado_id,
                operador_id,
                autoatendimento,
                novo_status,
            )
            if row is None:
                return None
            op_efetivo = str(row["operador_id"]) if row["operador_id"] else operador_id
            await self._registrar(
                conn, chamado_id, claims["sub"], "ATENDIMENTO_INICIADO",
                {"operador_id": op_efetivo},
            )
            return dict(row)
```

- [ ] **Step 2: Commit da Task 2**

```bash
git add app/repositories/atendimento.py
git commit -m "feat(atendimento): preservar operador pre-definido ao iniciar atendimento com COALESCE"
```

---

### Task 3: Rotas e Templates do Workspace — Injeção de Contexto e Renderização da UI

**Files:**
- Modify: `app/routes/workspace.py:506-565`
- Modify: `app/templates/workspace/atendimento.html:322-378,452-595`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `perm.pode_atribuir`, `perm.pode_iniciar`.
- Produces: Contexto `pode_atribuir` e `pode_iniciar` em `_carregar_atendimento`; formulário de atribuição visível no Marketing em chamados `NOVO`/`A_FAZER`; botão "Iniciar atendimento" condicionado a `pode_iniciar`.

- [ ] **Step 1: Escrever teste de rota para exibição de atribuição no Marketing em `tests/test_workspace.py`**

Adicionar teste em `tests/test_workspace.py`:

```python
def test_marketing_exibe_atribuicao_e_iniciar_em_novo_sem_operador():
    repo = FakeRepo(departamento="Marketing", status="NOVO", operador_id=None)
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert 'action="/workspace/chamados/c1/atribuir"' in r.text
    assert 'name="operador_id"' in r.text
    assert 'action="/workspace/chamados/c1/iniciar"' in r.text
    assert "Iniciar atendimento" in r.text


def test_marketing_com_operador_em_novo_exibe_composer_e_iniciar():
    repo = FakeRepo(departamento="Marketing", status="NOVO", operador_id=OP)
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert 'id="composer"' in r.text
    assert 'action="/workspace/chamados/c1/iniciar"' in r.text
    assert "Iniciar atendimento" in r.text
```

- [ ] **Step 2: Executar testes de rota para verificar que falha**

Run: `pytest tests/test_workspace.py -k "test_marketing_exibe_atribuicao" -v`
Expected: FAIL porque a UI ainda esconde `atribuir` e `iniciar`.

- [ ] **Step 3: Injetar `pode_atribuir` e `pode_iniciar` em `app/routes/workspace.py`**

Em `_carregar_atendimento`:
```python
    pode_reivindicar = perm.pode_reivindicar
    pode_atender = perm.pode_atender
    pode_atribuir = perm.pode_atribuir
    pode_iniciar = perm.pode_iniciar
```
E incluir `"pode_atribuir": pode_atribuir` e `"pode_iniciar": pode_iniciar` no dicionário `ctx_render`.

- [ ] **Step 4: Atualizar `app/templates/workspace/atendimento.html`**

1. No banner de aviso (linhas ~322-332):
```jinja
      {% elif not pode_atender %}
      {% if pode_iniciar %}
      <p class="mt-4 rounded-lg bg-amber-50 border border-amber-200 px-3.5 py-2.5 text-xs text-amber-800">
        Este chamado do seu setor ainda não foi assumido por ninguém — {% if pode_atribuir %}defina um operador ou {% endif %}clique em
        <strong>Iniciar atendimento</strong> para liberar a resposta.
      </p>
      {% else %}
      <p class="mt-4 rounded-lg bg-navy-50 border border-navy-100 px-3.5 py-2.5 text-xs text-navy">
        Você está <strong>acompanhando</strong> este chamado (aberto por alguém do seu setor) —
        só quem atende a fila do departamento pode responder ou alterá-lo.
      </p>
      {% endif %}
```

2. No botão de início (linhas ~365-378):
```jinja
    {% if pode_iniciar %}
    {# Iniciar atendimento: assume o chamado e move para "Em atendimento" #}
    <form method="post" action="/workspace/chamados/{{ chamado.id }}/iniciar{% if origem %}?origem={{ origem }}{% endif %}">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
      <button type="submit"
              class="w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-navy-900 text-white text-sm font-semibold hover:bg-navy-800 shadow-soft transition">
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3l14 9-14 9V3z"/></svg>
        Iniciar atendimento
      </button>
    </form>
    {% endif %}
```

3. No card de Ações (linhas ~452-563):
Substituir o gate do card de Ações para abrir quando `pode_atender or pode_atribuir`:
```jinja
    {% if pode_atender or pode_atribuir %}
    <div class="bg-white rounded-xl shadow-card border border-line/60 p-5 space-y-4">
      <h3 class="font-display font-bold text-navy">Ações</h3>
...
      {# formulários de status, prioridade, categoria e atribuir #}
...
    </div>
    {% endif %}
```
Mantendo o encerramento em destaque restrito a `{% if pode_atender and chamado.status != 'RESOLVIDO' %}` e combinação de duplicados restrita a `{% if pode_atender and not eh_duplicado %}`.

- [ ] **Step 5: Executar testes de rota para verificar que passam**

Run: `pytest tests/test_workspace.py -k "test_marketing" -v`
Expected: PASS.

- [ ] **Step 6: Executar a suíte de testes completa do workspace e atendimento**

Run: `pytest tests/test_atendimento_service.py tests/test_workspace.py -v`
Expected: 100% PASS.

- [ ] **Step 7: Commit da Task 3**

```bash
git add app/routes/workspace.py app/templates/workspace/atendimento.html tests/test_workspace.py
git commit -m "feat(workspace): habilitar definicao de operador em novo e a fazer no marketing"
```

---

### Task 4: Atualização de Documentação Mestre e Changelog

**Files:**
- Modify: `plano_mestre_desenvolvimento.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Produces: Registro formal da melhoria na tabela de estado e changelog do projeto.

- [ ] **Step 1: Atualizar `plano_mestre_desenvolvimento.md`**

Registrar no histórico e na Seção 5.2 (Marketing) a regra de atribuição de operador em chamados `NOVO` e `A_FAZER` sem necessidade de iniciar atendimento.

- [ ] **Step 2: Atualizar `docs/CHANGELOG.md`**

Adicionar entrada com a data atual descrevendo a melhoria implementada.

- [ ] **Step 3: Commit da Task 4**

```bash
git add plano_mestre_desenvolvimento.md docs/CHANGELOG.md
git commit -m "docs(changelog): documentar liberacao de operador em chamados novo/a fazer no marketing"
```

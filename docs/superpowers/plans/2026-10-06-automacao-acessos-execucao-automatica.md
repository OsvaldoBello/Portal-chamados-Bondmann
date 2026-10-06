# Automação de Acessos — Execução Automática e Alerta de Erro TI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tornar automática a execução de chamados de criação e desligamento de acessos no Portal de Chamados (sem exigir o clique/aval prévio de aprovação do operador), respeitando as regras de agendamento, interrompendo o fluxo em falhas e enviando alerta detalhado com logs e etapa para `ti@bondmann.com.br`.

**Architecture:** 
1. Na abertura de chamado estruturado de criação ou desligamento (`app/routes/portal.py`), o portal enfileira automaticamente o job com status `NA_FILA` via `automacao_svc.enfileirar_automatico`.
2. O agendamento é calculado respeitando feriados e horários padrão (07h dia útil anterior para criação, 17h para desligamento, ou imediato se urgente/passado).
3. O e-mail `ti@bondmann.com.br` torna-se destinatário fixo e garantido em `destinatarios_alerta` para qualquer erro/pendência/travamento de job.
4. A atualização de conta no portal (`_criar_conta_portal`) passa a usar conexão administrativa (`admin_connection`) para evitar bloqueios de RLS.

**Tech Stack:** Python 3.12, FastAPI, asyncpg (PostgreSQL com RLS), Pytest.

## Global Constraints
- `ti@bondmann.com.br` deve sempre receber o e-mail de alerta detalhado em caso de erro.
- A abertura de chamado pelo RH nunca deve falhar se a automação encontrar erro ao enfileirar.
- O chamado só é resolvido se todas as etapas concluírem com `SUCCESS`.
- `AUTOMACAO_ATIVA=false` deve continuar funcionando como kill switch global.

---

### Task 1: Configurar destinatário obrigatório `ti@bondmann.com.br` e alerta em jobs travados

**Files:**
- Modify: `app/services/automacao.py:279-303`
- Modify: `app/services/automacao.py:525-555`
- Test: `tests/test_automacao.py`

**Interfaces:**
- Consumes: `Settings.automacao_alerta_email`
- Produces: `destinatarios_alerta(settings: Settings) -> list[str]` sempre contendo `"ti@bondmann.com.br"`.

- [ ] **Step 1: Write the failing test for `destinatarios_alerta`**

Adicionar em `tests/test_automacao.py`:
```python
def test_destinatarios_alerta_sempre_inclui_ti_bondmann(settings_automacao, monkeypatch):
    monkeypatch.setattr(settings_automacao, "automacao_alerta_email", "")
    destinos = svc.destinatarios_alerta(settings_automacao)
    assert "ti@bondmann.com.br" in destinos

def test_destinatarios_alerta_combina_com_env(settings_automacao, monkeypatch):
    monkeypatch.setattr(settings_automacao, "automacao_alerta_email", "admin@bondmann.com.br;outro@bondmann.com.br")
    destinos = svc.destinatarios_alerta(settings_automacao)
    assert "ti@bondmann.com.br" in destinos
    assert "admin@bondmann.com.br" in destinos
    assert "outro@bondmann.com.br" in destinos
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automacao.py -k test_destinatarios_alerta -v`
Expected: FAIL (porque `destinatarios_alerta` retorna lista vazia quando a env é vazia)

- [ ] **Step 3: Implement `destinatarios_alerta` and `vigiar_uma_vez` notification**

Em `app/services/automacao.py`:
```python
EMAIL_TI_PADRAO = "ti@bondmann.com.br"

def destinatarios_alerta(settings: Settings) -> list[str]:
    """`AUTOMACAO_ALERTA_EMAIL` aceita vários endereços separados por vírgula
    ou ponto-e-vírgula. `ti@bondmann.com.br` é sempre incluído como padrão."""
    brutos = (settings.automacao_alerta_email or "").replace(";", ",").split(",")
    vistos: list[str] = [EMAIL_TI_PADRAO]
    for e in (b.strip().lower() for b in brutos):
        if e and "@" in e and e not in vistos:
            vistos.append(e)
    return vistos
```

E em `vigiar_uma_vez`:
Quando `requeue` for `False` (job FALHOU após estourar tentativas ou timeout):
```python
        if not requeue:
            await _email_alerta_ti(
                settings,
                f"[Automação de acessos] FALHOU (timeout) — chamado {job.get('chamado_codigo') or job.get('id')}",
                f"Automação travada sem heartbeat:\n{motivo}\n\nLink: {site}/workspace/chamados/{job.get('chamado_id')}",
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_automacao.py -k test_destinatarios_alerta -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/automacao.py tests/test_automacao.py
git commit -m "feat(automacao): garantir ti@bondmann.com.br nos alertas e avisar em timeout"
```

---

### Task 2: Implementar resolução de perfil da TI no repositório administrativo

**Files:**
- Modify: `app/repositories/automacao.py`
- Test: `tests/test_automacao.py`

**Interfaces:**
- Consumes: Conexão administrativa do banco (`admin_connection`)
- Produces: `admin_obter_perfil_ti_id() -> str | None`

- [ ] **Step 1: Write test for `admin_obter_perfil_ti_id`**

Adicionar em `tests/test_automacao.py`:
```python
@pytest.mark.asyncio
async def test_admin_obter_perfil_ti_id_mock(monkeypatch):
    async def fake_fetchval(query, *args):
        if "ti@bondmann.com.br" in query or "TI" in query:
            return "perfil-ti-uuid"
        return None
    class FakeConn:
        fetchval = fake_fetchval
    @contextmanager
    async def fake_admin_conn():
        yield FakeConn()
    monkeypatch.setattr(repo_admin, "admin_connection", fake_admin_conn)
    perfil_id = await repo_admin.admin_obter_perfil_ti_id()
    assert perfil_id == "perfil-ti-uuid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automacao.py -k test_admin_obter_perfil_ti_id -v`
Expected: FAIL (função não existe)

- [ ] **Step 3: Implement `admin_obter_perfil_ti_id`**

Em `app/repositories/automacao.py`:
```python
async def admin_obter_perfil_ti_id() -> str | None:
    """Busca o id do perfil ti@bondmann.com.br ou do primeiro admin/operador
    do departamento TI para autoria de jobs automáticos."""
    async with admin_connection() as conn:
        # 1. Procura perfil direto da caixa de grupo ti@bondmann.com.br
        perfil_id = await conn.fetchval(
            "SELECT id::text FROM perfis WHERE lower(email) = 'ti@bondmann.com.br' LIMIT 1"
        )
        if perfil_id:
            return str(perfil_id)
        # 2. Fallback para qualquer admin/operador do TI
        perfil_id = await conn.fetchval(
            """SELECT p.id::text FROM perfis p
                 JOIN departamentos d ON d.id = p.departamento_id
                WHERE d.ativo AND lower(d.nome) = 'ti' AND p.role IN ('ADMIN', 'OPERADOR')
                ORDER BY p.role = 'ADMIN' DESC, p.created_at ASC LIMIT 1"""
        )
        return str(perfil_id) if perfil_id else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_automacao.py -k test_admin_obter_perfil_ti_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/repositories/automacao.py tests/test_automacao.py
git commit -m "feat(automacao): adicionar admin_obter_perfil_ti_id no repositorio"
```

---

### Task 3: Implementar `enfileirar_automatico` e resiliência em `_criar_conta_portal`

**Files:**
- Modify: `app/services/automacao.py`
- Test: `tests/test_automacao.py`

**Interfaces:**
- Consumes: `chamado: dict[str, Any]`, `settings: Settings`
- Produces: `enfileirar_automatico(chamado: dict[str, Any], settings: Settings | None = None) -> dict[str, Any] | None`

- [ ] **Step 1: Write tests for `enfileirar_automatico`**

Em `tests/test_automacao.py`:
```python
@pytest.mark.asyncio
async def test_enfileirar_automatico_criacao(monkeypatch, settings_automacao):
    chamado = {
        "id": "c1",
        "codigo": "BD-2026-00001",
        "subcategoria": ac.SUB_CRIACAO_USUARIO,
        "dados_formulario": DADOS_CRIACAO,
        "cliente_id": "usr-rh-1",
    }
    agendado = {}
    async def fake_agendar(**kwargs):
        agendado.update(kwargs)
        return {"id": "job-1", "status": "NA_FILA", **kwargs}

    async def fake_historico(*args, **kwargs):
        pass

    monkeypatch.setattr(repo_admin, "admin_agendar_job", fake_agendar)
    monkeypatch.setattr(repo_admin, "admin_feriados_entre", lambda *a: set())
    monkeypatch.setattr(repo_admin, "admin_obter_perfil_ti_id", lambda: "ti-profile-id")
    monkeypatch.setattr(repo_admin, "admin_registrar_historico", fake_historico)

    job = await svc.enfileirar_automatico(chamado, settings=settings_automacao)
    assert job is not None
    assert job["tipo"] == "CRIACAO"
    assert agendado["aprovado_por"] == "ti-profile-id"
    assert agendado["chamado_id"] == "c1"

@pytest.mark.asyncio
async def test_enfileirar_automatico_respeita_kill_switch(monkeypatch, settings_automacao):
    monkeypatch.setattr(settings_automacao, "automacao_ativa", False)
    chamado = {
        "id": "c1",
        "codigo": "BD-2026-00001",
        "subcategoria": ac.SUB_CRIACAO_USUARIO,
        "dados_formulario": DADOS_CRIACAO,
        "cliente_id": "usr-rh-1",
    }
    job = await svc.enfileirar_automatico(chamado, settings=settings_automacao)
    assert job is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automacao.py -k test_enfileirar_automatico -v`
Expected: FAIL (`enfileirar_automatico` não existe)

- [ ] **Step 3: Implement `enfileirar_automatico` and admin connection in `_criar_conta_portal`**

Em `app/services/automacao.py`:
```python
async def enfileirar_automatico(
    chamado: dict[str, Any],
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Enfileira automaticamente o job para chamados de Criação/Desligamento
    na abertura do chamado (sem exigir aprovação manual do operador)."""
    settings = settings or get_settings()
    if not settings.automacao_ativa:
        log.info("[AUTOMACAO] job automático não enfileirado: automação desativada")
        return None

    tipo = dom.tipo_da_subcategoria(chamado.get("subcategoria"))
    dados = chamado.get("dados_formulario") or {}
    if tipo is None or not dados:
        return None

    if tipo not in tipos_liberados(settings):
        log.info("[AUTOMACAO] job automático não enfileirado: tipo %s não liberado", tipo)
        return None

    chamado_id = str(chamado.get("id") or "")
    if not chamado_id:
        return None

    # Resolve perfil aprovador (preferência para TI; fallback para cliente_id)
    aprovador_id = await repo_admin.admin_obter_perfil_ti_id()
    if not aprovador_id:
        aprovador_id = str(chamado.get("cliente_id") or "")
    if not aprovador_id:
        log.warning("[AUTOMACAO] chamado %s sem autor/TI para enfileirar job", chamado_id)
        return None

    payload = dom.montar_payload(tipo, dados, chamado)
    agora = datetime.now(UTC)
    feriados = await repo_admin.admin_feriados_entre(
        agora.date() - timedelta(days=1), agora.date() + timedelta(days=400)
    )
    executar_apos = dom.calcular_executar_apos(
        tipo,
        payload,
        agora=agora,
        feriados=feriados,
        hora_criacao=settings.automacao_hora_criacao,
        hora_desligamento=settings.automacao_hora_desligamento,
        licenca_dias=settings.automacao_licenca_dias,
    )

    job = await repo_admin.admin_agendar_job(
        chamado_id=chamado_id,
        tipo=tipo,
        payload=payload,
        executar_apos=executar_apos,
        aprovado_por=aprovador_id,
    )
    if job:
        await repo_admin.admin_registrar_historico(
            chamado_id,
            aprovador_id,
            "AUTOMACAO_ENFILEIRADA",
            {
                "job_id": str(job["id"]),
                "tipo": tipo,
                "executar_apos": executar_apos.isoformat(),
                "automatico": True,
            },
        )
        log.info(
            "[AUTOMACAO] job %s (%s) enfileirado automaticamente para chamado %s (executar_apos: %s)",
            job["id"], tipo, chamado.get("codigo") or chamado_id, executar_apos.isoformat(),
        )
    return job
```

E em `_criar_conta_portal`, substituir a atualização de papel/setor por `admin_connection()`:
```python
    from app.db import admin_connection

    async with admin_connection() as conn:
        dep_id = await conn.fetchval(
            "SELECT id::text FROM departamentos WHERE ativo AND lower(nome) = lower($1)", setor_nome
        )
    if dep_id is None:
        return False, f'setor "{setor_nome}" não encontrado no Portal'
...
    # Promove papel + setor usando admin_connection (evita restrições de RLS do autor)
    async with admin_connection() as conn:
        await conn.execute(
            "UPDATE perfis SET nome = $2, role = $3::papel_usuario, departamento_id = $4::uuid WHERE id = $1::uuid",
            user_id, nome, papel, dep_id,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_automacao.py -k test_enfileirar_automatico -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/automacao.py tests/test_automacao.py
git commit -m "feat(automacao): implementar enfileirar_automatico e admin_connection em criar_conta"
```

---

### Task 4: Integrar enfileiramento na rota `criar_chamado`

**Files:**
- Modify: `app/routes/portal.py:740-750`
- Test: `tests/test_portal.py`

**Interfaces:**
- Consumes: `novo`, `nome_subcategoria_val`, `dados_formulario_val`
- Produces: Chamada a `automacao_svc.enfileirar_automatico` após `commit_now()`

- [ ] **Step 1: Write integration test in `test_portal.py`**

Em `tests/test_portal.py`:
```python
def test_criar_chamado_dispara_enfileiramento_automacao(client_rh, monkeypatch):
    enfileirado = []
    async def fake_enfileirar(chamado, settings=None):
        enfileirado.append(chamado)
        return {"id": "job-1"}
    monkeypatch.setattr("app.services.automacao.enfileirar_automatico", fake_enfileirar)

    # Payload para criação de usuário
    # Asserção de que enfileirado recebeu o chamado com subcategoria e dados
```

- [ ] **Step 2: Add call in `app/routes/portal.py`**

Logo após `if triagem_cobre: triagem.agendar_triagem(...)`:
```python
    # Automação de acessos (Criação / Desligamento): enfileiramento automático
    tipo_automacao = dom_automacao.tipo_da_subcategoria(nome_subcategoria_val)
    if tipo_automacao and dados_formulario_val:
        try:
            from app.services import automacao as automacao_svc
            await automacao_svc.enfileirar_automatico(
                {
                    "id": str(novo["id"]),
                    "codigo": novo["codigo"],
                    "subcategoria": nome_subcategoria_val,
                    "dados_formulario": dados_formulario_val,
                    "cliente_id": ctx.user.id,
                }
            )
        except Exception:
            log.exception("[AUTOMACAO] falha ao enfileirar job automático para %s", novo["codigo"])
```

- [ ] **Step 3: Run portal and automacao tests**

Run: `pytest tests/test_automacao.py tests/test_portal.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add app/routes/portal.py tests/test_portal.py
git commit -m "feat(portal): disparar enfileiramento automatico na criacao do chamado"
```

---

### Task 5: Validação completa da suíte e atualização do plano mestre

**Files:**
- Modify: `plano_md_mestre_automacao_acessos.md`
- Run: Suíte de testes `pytest tests/test_automacao.py`

- [ ] **Step 1: Update documentation in `plano_md_mestre_automacao_acessos.md`**

Atualizar a Decisão D2 na Seção 0.2:
- Registrar que o gate humano de criação e desligamento foi desativado em 2026-10-06, passando a rodar automaticamente na abertura do chamado, com parada em caso de erro e alerta enviado para `ti@bondmann.com.br`.

- [ ] **Step 2: Run full regression tests**

Run: `pytest tests/test_automacao.py tests/test_portal.py`
Expected: All tests PASS.

- [ ] **Step 3: Commit**

```bash
git add plano_md_mestre_automacao_acessos.md
git commit -m "docs(automacao): documentar automacao automatica sem gate humano no plano mestre"
```

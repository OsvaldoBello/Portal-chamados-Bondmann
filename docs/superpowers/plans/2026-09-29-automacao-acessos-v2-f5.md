# Automação de Acessos v2 — F5 (sincronização de liderança na UBD) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Manter, todo dia e a cada troca de supervisor/gerente, a liderança de representantes e supervisores na UBD igual à do SAP — adicionando os líderes esperados e removendo só os líderes "comerciais" que não valem mais (espelho com proteção, V4).

**Architecture:** Novo tipo de job `SINCRONIZAR_LIDERANCA` na fila existente (`automacao_jobs`), **sem chamado**. O portal só enfileira (diário às 06h e por evento), guarda o histórico de líderes comerciais e distribui o relatório. O worker calcula tudo: lê a `IB_CO_REGIAO` e os e-mails dos PNs no SAP, confirma cada região na função `lideranca_da_regiao` (V14), lê os líderes atuais de cada usuário com `PATCH {}` e grava a lista completa com `PATCH {"leaders": [...]}` (o PATCH substitui — spike F5.0).

**Tech Stack:** Python 3.12, FastAPI/asyncpg (portal), Postgres/Supabase (migrations + RLS), pytest (+ e2e `-m rls`), worker `requests` + SAP B1 Service Layer + API Skore/UBD.

**Spec:** [`plano_md_mestre_automacao_acessos_v2.md`](../../../plano_md_mestre_automacao_acessos_v2.md) — Seção 0 (V1, V4, V14), Seção 6 (F5 e resultado do spike F5.0). Plano anterior (F3/F4): [`2026-09-25-automacao-acessos-v2-f3-f5.md`](2026-09-25-automacao-acessos-v2-f3-f5.md).

## Global Constraints

- **Fonte dos líderes = SAP; `regioes` só confirma** (via função `lideranca_da_regiao` com token — V14). Região **divergente** ⇒ os usuários dela ficam de fora e entram no relatório; região **não confirmada** ⇒ segue com o SAP.
- **Espelho com proteção (V4):** `novo = (atuais − (gerenciados − esperados)) ∪ esperados`. `gerenciados` = supervisores/gerentes do SAP **de hoje ∪ de antes** (tabela `automacao_lideranca_gerenciada`). Líder posto à mão nunca é removido.
- **O `PATCH` de `leaders` substitui a lista** ⇒ sempre mandar a lista **completa**. **Leitura = `PATCH {}`** (devolve `leader_ids`); se a resposta não tiver `leader_ids`, a sincronização inteira **para** (`LeituraLiderancaIndisponivel`) — nunca tratar como lista vazia.
- **Modo `relatorio` é o default** (`AUTOMACAO_SYNC_MODO`); só manda `leaders` com `aplicar`. Simulação (`dry_run`) ⇒ sempre relatório.
- Só enfileira se `AUTOMACAO_TIPOS` contém `SINCRONIZAR_LIDERANCA` (fila vencida de tipo não liberado dispara o alerta de "worker mudo").
- Sync nunca tem `chamado_id` nem precisa de `aprovado_por` (CHECK na 0093). Chamado de origem (evento) vai no `payload.chamado`.
- **`regioes` é somente leitura** (V1) — nenhum código novo escreve nela.
- Contrato continua `"1"` (aditivo). **Ordem de deploy: worker → migrations 0092/0093 em produção → portal → env `AUTOMACAO_TIPOS`.**
- Worker: editar arquivos **preservando o fim de linha** (repo sem `autocrlf`; `worker.py`, `flow.py`, `services/sap_api.py`, `tests/test_worker.py` são CRLF; `services/learning_rocks.py`, `config/settings.py`, `models/factories.py` são LF). Nunca `git add -A`; nunca `pytest` sem arquivo.
- Portal: `npm run build:css` antes da suíte completa; `ruff check app tests` limpo; pytest com `-o addopts=""` para ver o resumo.
- Commits com `git commit -F <arquivo>` terminando em `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Nada de push/merge/deploy/migration em produção sem o "ok" do gestor.

## File Structure

| Arquivo | Repo | Responsabilidade |
|---|---|---|
| `services/learning_rocks.py` | worker | `LeituraLiderancaIndisponivel`, `get_leader_ids`, `set_leader_ids` |
| `services/lideranca.py` (novo) | worker | Regras puras: `Alvo`, `montar_alvos`, `nova_lista` |
| `services/sap_api.py` | worker | `mapa_lideranca()` (regiões + e-mail por PN, só GET) |
| `flow.py` | worker | `STEP_UBD_SYNC`, `run_leadership_sync_flow`, `_sincronizar_lideranca` |
| `worker.py` | worker | `TIPO_SINCRONIZAR_LIDERANCA` no `executar_job` |
| `tests/test_sync_lideranca.py` (novo) | worker | Testes da F5 no worker |
| `supabase/migrations/0092_…sql`, `0093_…sql` (novos) | portal | Enum, colunas opcionais, índice, tabela de gerenciados |
| `app/domain/automacao.py` | portal | Tipo, payload, gatilhos e textos da sync |
| `app/config.py` | portal | `automacao_sync_modo`, `automacao_sync_hora`, `automacao_sync_atraso_min` |
| `app/repositories/automacao.py` | portal | Queries sem `JOIN` obrigatório com chamados; gerenciados; última sync |
| `app/services/automacao.py` | portal | `agendar_sincronizacao`, `processar_resultado_sync`, gatilhos |
| `app/routes/automacao_api.py` | portal | `_job_publico` com `chamado` nulo |
| `tests/test_automacao.py`, `tests/e2e/test_rls_automacao_sync.py` (novo) | portal | Testes |
| `docs/automacao_api.md`, `docs/CHANGELOG.md`, `plano_md_mestre_automacao_acessos.md` (Seção 5), `plano_md_mestre_automacao_acessos_v2.md`, `PLANO_MESTRE_AUTOMACAO.md` | ambos | Docs |

---

## Worker (`C:\Users\Osvaldo\Downloads\Automação`)

Antes de tudo: `cd "C:/Users/Osvaldo/Downloads/Automação" && git checkout master && git pull --ff-only && git checkout -b feat/v2-f5-sync`.

### Task 1: Ler e gravar líderes na UBD

**Files:**
- Modify: `services/learning_rocks.py` (exceção no topo do módulo; dois métodos na classe, depois de `reactivate_user`)
- Test: `tests/test_sync_lideranca.py` (novo)

**Interfaces:**
- Produces: `LeituraLiderancaIndisponivel(RuntimeError)`; `LearningRocksService.get_leader_ids(user_id: int) -> List[int]`; `LearningRocksService.set_leader_ids(user_id: int, leader_ids: List[int]) -> None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sync_lideranca.py
"""F5 da v2: sincronização de liderança na UBD (espelho com proteção, V4).
Rodar: python -m pytest tests/test_sync_lideranca.py -q"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.learning_rocks import LearningRocksService, LeituraLiderancaIndisponivel  # noqa: E402


class _Resp:
    def __init__(self, status, corpo):
        self.status_code = status
        self._corpo = corpo
        self.text = str(corpo)

    def json(self):
        return self._corpo


def _ubd(monkeypatch, respostas):
    chamadas = []

    def fake_patch(url, headers=None, json=None, timeout=None):
        chamadas.append((url, json))
        return respostas.pop(0)

    monkeypatch.setattr("services.learning_rocks.requests.patch", fake_patch)
    return LearningRocksService(api_token="t", base_url="https://ubd.test"), chamadas


def test_leitura_de_lideres_e_um_patch_vazio(monkeypatch):
    svc, chamadas = _ubd(monkeypatch, [_Resp(200, {"id": 7, "leader_ids": [40, 41]})])
    assert svc.get_leader_ids(7) == [40, 41]
    assert chamadas == [("https://ubd.test/workspace/v1/users/7", {})]


def test_leitura_sem_leader_ids_interrompe(monkeypatch):
    svc, _ = _ubd(monkeypatch, [_Resp(200, {"id": 7})])
    with pytest.raises(LeituraLiderancaIndisponivel):
        svc.get_leader_ids(7)


def test_gravacao_manda_a_lista_completa_e_confere(monkeypatch):
    svc, chamadas = _ubd(monkeypatch, [_Resp(200, {"id": 7, "leader_ids": [41, 40]})])
    svc.set_leader_ids(7, [40, 41])
    assert chamadas == [("https://ubd.test/workspace/v1/users/7", {"leaders": [40, 41]})]


def test_gravacao_que_nao_confere_falha(monkeypatch):
    svc, _ = _ubd(monkeypatch, [_Resp(200, {"id": 7, "leader_ids": [40]})])
    with pytest.raises(RuntimeError, match="gravou"):
        svc.set_leader_ids(7, [40, 41])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_lideranca.py -q`
Expected: FAIL — `ImportError: cannot import name 'LeituraLiderancaIndisponivel'`.

- [ ] **Step 3: Write minimal implementation**

`services/learning_rocks.py`, logo antes de `class LearningRocksService:`:

```python
class LeituraLiderancaIndisponivel(RuntimeError):
    """O `PATCH {}` deixou de devolver `leader_ids` — a UBD mudou de
    comportamento; a sincronização de liderança (plano v2, F5) para inteira."""
```

Na classe, depois de `reactivate_user`:

```python
    def get_leader_ids(self, user_id: int) -> List[int]:
        """Líderes atuais do usuário. A API não tem GET de líderes (spike F5.0,
        2026-09-29): `PATCH {}` não altera nada e devolve o usuário com `leader_ids`."""
        url = f"{self.base_url}/workspace/v1/users/{user_id}"
        resp = requests.patch(url, headers=self._headers(), json={}, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"UBD respondeu {resp.status_code} ao ler os líderes do usuário {user_id}: {resp.text[:200]}")
        corpo = resp.json()
        ids = corpo.get("leader_ids") if isinstance(corpo, dict) else None
        if not isinstance(ids, list):
            raise LeituraLiderancaIndisponivel(
                f"a UBD não devolveu leader_ids no PATCH vazio (usuário {user_id}) — "
                "comportamento mudou; sincronização interrompida"
            )
        return [int(i) for i in ids]

    def set_leader_ids(self, user_id: int, leader_ids: List[int]) -> None:
        """Grava a lista COMPLETA de líderes (o PATCH substitui — spike F5.0) e
        confere o que a UBD guardou."""
        url = f"{self.base_url}/workspace/v1/users/{user_id}"
        resp = requests.patch(url, headers=self._headers(), json={"leaders": list(leader_ids)}, timeout=20)
        if resp.status_code != 200:
            raise RuntimeError(f"UBD respondeu {resp.status_code} ao gravar os líderes do usuário {user_id}: {resp.text[:200]}")
        corpo = resp.json()
        gravados = corpo.get("leader_ids") if isinstance(corpo, dict) else None
        if not isinstance(gravados, list) or sorted(int(i) for i in gravados) != sorted(int(i) for i in leader_ids):
            raise RuntimeError(f"UBD gravou {gravados} como líderes do usuário {user_id}, esperado {sorted(leader_ids)}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sync_lideranca.py tests/test_lideranca.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `git add services/learning_rocks.py tests/test_sync_lideranca.py`; mensagem `feat(ubd): ler lideres por PATCH vazio e gravar a lista completa (v2 F5)`.

### Task 2: Regras puras da sincronização

**Files:**
- Create: `services/lideranca.py` (LF)
- Test: `tests/test_sync_lideranca.py`

**Interfaces:**
- Produces:
  - `Alvo(email: str, perfil: str, regioes: Set[str], esperados: Set[str])` (dataclass)
  - `montar_alvos(regioes: List[Dict[str, Any]], email_pn: Dict[str, Optional[str]], divergentes: Set[str]) -> Tuple[Dict[str, Alvo], List[Dict[str, str]], Set[str]]` → (alvos por e-mail, pulados `[{email, regioes}]`, líderes comerciais de hoje)
  - `nova_lista(atuais: List[int], esperados: Set[int], gerenciados: Set[int]) -> List[int]`

- [ ] **Step 1: Write the failing test** (acrescentar em `tests/test_sync_lideranca.py`)

```python
from services.lideranca import montar_alvos, nova_lista  # noqa: E402

EMAILS = {
    "F001": "rep.um@bondmann.com.br", "F002": "rep.dois@bondmann.com.br",
    "RH2052": "jorge@bondmann.com.br", "RH2004": "bruno@bondmann.com.br",
    "RH2021": None, "RH2020": None,
}
REGIOES = [
    {"Code": "001-A", "U_IB_CodCom1": "F001", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"},
    {"Code": "002-B", "U_IB_CodCom1": "F001", "U_IB_CodCom3": "RH2021", "U_IB_CodCom4": "RH2004"},  # vaga aberta
    {"Code": "003-C", "U_IB_CodCom1": "F002", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"},
    {"Code": "004-D", "U_IB_CodCom1": "RH2020", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"},  # sem representante
]


def test_montar_alvos_representante_e_supervisor():
    alvos, pulados, comerciais = montar_alvos(REGIOES, EMAILS, set())
    assert alvos["rep.um@bondmann.com.br"].esperados == {"jorge@bondmann.com.br", "bruno@bondmann.com.br"}
    assert alvos["rep.um@bondmann.com.br"].regioes == {"001-A", "002-B"}
    assert alvos["jorge@bondmann.com.br"].perfil == "SUPERVISOR"
    assert alvos["jorge@bondmann.com.br"].esperados == {"bruno@bondmann.com.br"}
    assert "bruno@bondmann.com.br" not in alvos  # gerente fica sem líder (V5)
    assert pulados == [] and comerciais == {"jorge@bondmann.com.br", "bruno@bondmann.com.br"}


def test_regiao_divergente_tira_do_calculo_quem_depende_dela():
    alvos, pulados, _ = montar_alvos(REGIOES, EMAILS, {"003-C"})
    assert "rep.dois@bondmann.com.br" not in alvos and "jorge@bondmann.com.br" not in alvos
    assert "rep.um@bondmann.com.br" in alvos
    assert {p["email"] for p in pulados} == {"rep.dois@bondmann.com.br", "jorge@bondmann.com.br"}


def test_pessoa_nunca_e_lider_de_si_mesma():
    regioes = [{"Code": "005-E", "U_IB_CodCom1": "RH2052", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"}]
    alvos, _, _ = montar_alvos(regioes, EMAILS, set())
    assert alvos["jorge@bondmann.com.br"].esperados == {"bruno@bondmann.com.br"}


def test_nova_lista_preserva_manual_troca_comercial_e_mantem_ordem():
    # 99 = líder posto à mão; 21 = supervisor antigo (gerenciado); 20/30 = esperados
    assert nova_lista([99, 21], {20, 30}, {20, 21, 30}) == [99, 20, 30]
    assert nova_lista([20, 30], {20, 30}, {20, 30}) == [20, 30]  # nada muda
    assert nova_lista([], set(), {20}) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_lideranca.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'services.lideranca'`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/lideranca.py
"""Sincronização de liderança na UBD (plano v2, F5) — regras puras, sem rede.

Esperados (V5): representante = supervisor + gerente das regiões em que é o
`U_IB_CodCom1`; supervisor = gerentes das regiões em que é o `U_IB_CodCom3`;
gerente fica sem líder. Marcador de vaga (PN sem e-mail) não vira líder."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

REPRESENTANTE = "REPRESENTANTE"
SUPERVISOR = "SUPERVISOR"


@dataclass
class Alvo:
    email: str
    perfil: str
    regioes: Set[str] = field(default_factory=set)
    esperados: Set[str] = field(default_factory=set)


def _email(email_pn: Dict[str, Optional[str]], card_code: Optional[str]) -> Optional[str]:
    email = (email_pn.get(card_code or "") or "").strip().lower()
    return email or None


def montar_alvos(
    regioes: List[Dict[str, Any]],
    email_pn: Dict[str, Optional[str]],
    divergentes: Set[str],
) -> Tuple[Dict[str, Alvo], List[Dict[str, str]], Set[str]]:
    """(alvos por e-mail, pulados por região divergente, líderes comerciais de hoje)."""
    alvos: Dict[str, Alvo] = {}
    comerciais: Set[str] = set()

    def _alvo(email: str, perfil: str) -> Alvo:
        if email not in alvos:
            alvos[email] = Alvo(email=email, perfil=perfil)
        return alvos[email]

    for r in regioes:
        code = r.get("Code") or ""
        rep = _email(email_pn, r.get("U_IB_CodCom1"))
        sup = _email(email_pn, r.get("U_IB_CodCom3"))
        ger = _email(email_pn, r.get("U_IB_CodCom4"))
        comerciais.update(e for e in (sup, ger) if e)
        if rep:
            a = _alvo(rep, REPRESENTANTE)
            a.regioes.add(code)
            a.esperados.update(e for e in (sup, ger) if e)
        if sup:
            a = _alvo(sup, SUPERVISOR)
            a.regioes.add(code)
            if ger:
                a.esperados.add(ger)

    pulados: List[Dict[str, str]] = []
    for email in sorted(alvos):
        alvo = alvos[email]
        alvo.esperados.discard(email)
        tocadas = sorted(alvo.regioes & divergentes)
        if tocadas:
            pulados.append({"email": email, "regioes": ", ".join(tocadas)})
            del alvos[email]
    return alvos, pulados, comerciais


def nova_lista(atuais: List[int], esperados: Set[int], gerenciados: Set[int]) -> List[int]:
    """Espelho com proteção (V4): tira só os comerciais que não valem mais,
    mantém os manuais na ordem atual e acrescenta os esperados que faltam."""
    remover = gerenciados - esperados
    mantidos = [i for i in atuais if i not in remover]
    return mantidos + sorted(i for i in esperados if i not in mantidos)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sync_lideranca.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `git add services/lideranca.py tests/test_sync_lideranca.py`; mensagem `feat(lideranca): regras puras do espelho com protecao (v2 F5)`.

### Task 3: Mapa de liderança no SAP

**Files:**
- Modify: `services/sap_api.py` (método novo logo depois de `gerentes_da_vaga`) — CRLF
- Test: `tests/test_sync_lideranca.py`

**Interfaces:**
- Consumes: `email_do_pn(card_code) -> Optional[str]` (F4).
- Produces: `SAPService.mapa_lideranca() -> Tuple[List[Dict[str, Any]], Dict[str, Optional[str]]]` → (regiões `{Code, U_IB_CodCom1, U_IB_CodCom3, U_IB_CodCom4}`, e-mail por CardCode citado).

- [ ] **Step 1: Write the failing test**

```python
from tests.test_vagas import FakeSAPRegioes, sap_regioes  # noqa: E402,F401  (fixture reaproveitada)


def test_mapa_lideranca_le_regioes_e_emails_dos_pns(sap_regioes):
    pns = (
        {"CardCode": "F001", "CardName": "001 - REP UM", "CardType": "cSupplier", "EmailAddress": "rep.um@bondmann.com.br"},
        {"CardCode": "RH2052", "CardName": "JORGE", "CardType": "cSupplier", "EmailAddress": "jorge@bondmann.com.br"},
        {"CardCode": "RH2004", "CardName": "BRUNO", "CardType": "cSupplier", "EmailAddress": "bruno@bondmann.com.br"},
        {"CardCode": "RH2021", "CardName": "EQUIPE MG 2", "CardType": "cSupplier", "EmailAddress": None},
    )
    _, svc = sap_regioes([
        {"Code": "001-A", "U_IB_CodCom1": "F001", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"},
        {"Code": "002-B", "U_IB_CodCom1": "RH2020", "U_IB_CodCom3": "RH2021", "U_IB_CodCom4": "RH2004"},
    ], pns=pns)
    regioes, emails = svc.mapa_lideranca()
    assert [r["Code"] for r in regioes] == ["001-A", "002-B"]
    assert emails == {
        "F001": "rep.um@bondmann.com.br", "RH2004": "bruno@bondmann.com.br", "RH2020": None,
        "RH2021": None, "RH2052": "jorge@bondmann.com.br",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_lideranca.py -q -k mapa`
Expected: FAIL — `AttributeError: 'SAPService' object has no attribute 'mapa_lideranca'`.

- [ ] **Step 3: Write minimal implementation** (preservando CRLF)

```python
    def mapa_lideranca(self) -> Tuple[List[Dict[str, Any]], Dict[str, Optional[str]]]:
        """Todas as regiões (representante, supervisor, gerente) + o e-mail de
        cada PN citado — insumo da sincronização de liderança (plano v2, F5). Só GET."""
        self.login()
        regioes: List[Dict[str, Any]] = []
        url: Optional[str] = f"{self.base_url}/IB_CO_REGIAO"
        params: Optional[Dict[str, str]] = {"$select": "Code,U_IB_CodCom1,U_IB_CodCom3,U_IB_CodCom4"}
        while url:
            resp = self.session.get(url, params=params, timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(f"SAP respondeu {resp.status_code} ao ler a IB_CO_REGIAO: {resp.text[:300]}")
            corpo = resp.json()
            regioes += corpo.get("value", [])
            prox = corpo.get("odata.nextLink")
            url = (prox if prox.startswith("http") else f"{self.base_url}/{prox.lstrip('/')}") if prox else None
            params = None
        codigos = sorted({
            c for r in regioes
            for c in (r.get("U_IB_CodCom1"), r.get("U_IB_CodCom3"), r.get("U_IB_CodCom4")) if c
        })
        return regioes, {c: self.email_do_pn(c) for c in codigos}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sync_lideranca.py tests/test_vagas.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `git add services/sap_api.py tests/test_sync_lideranca.py`; mensagem `feat(sap): mapa de lideranca de todas as regioes (v2 F5)`.

### Task 4: Fluxo de sincronização e tipo de job no worker

**Files:**
- Modify: `flow.py` (imports, constante, dois métodos na classe) — CRLF
- Modify: `worker.py` (`TIPO_SINCRONIZAR_LIDERANCA`, `TIPOS`, ramo em `executar_job`) — CRLF
- Test: `tests/test_sync_lideranca.py`

**Interfaces:**
- Consumes: Tasks 1–3; `self.regioes.linha(code)` e `confirmar_lideres` (F4); `find_user_by_email` (UBD).
- Produces: `STEP_UBD_SYNC = "UBD Learning.rocks - Sincronização de Liderança"`; `AutomationFlowOrchestrator.run_leadership_sync_flow(modo: str, lideres_gerenciados: Iterable[str]) -> List[StepResult]`; `details` da etapa = `{modo, verificados, corrigidos: [{email, adicionados, removidos}], nao_encontrados, divergentes, regioes_nao_confirmadas, erros: [{email, erro}], avisos, lideres_comerciais}`; `worker.TIPO_SINCRONIZAR_LIDERANCA = "SINCRONIZAR_LIDERANCA"`.

- [ ] **Step 1: Write the failing test**

```python
from config import settings  # noqa: E402
from flow import STEP_UBD_SYNC, AutomationFlowOrchestrator  # noqa: E402
from models import StepResult  # noqa: E402

REGIOES_1 = [{"Code": "001-A", "U_IB_CodCom1": "F001", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"}]
EMAILS_1 = {"F001": "rep.um@bondmann.com.br", "RH2052": "jorge@bondmann.com.br", "RH2004": "bruno@bondmann.com.br"}
IDS = {"rep.um@bondmann.com.br": 10, "jorge@bondmann.com.br": 20, "bruno@bondmann.com.br": 30,
       "marcos@bondmann.com.br": 21}


class SapStub:
    def mapa_lideranca(self):
        return REGIOES_1, EMAILS_1

    def logout(self):
        return True


class RegioesStub:
    def __init__(self, linhas=None):
        self.linhas = linhas or {}

    def linha(self, code):
        return self.linhas.get(code)


class UbdStub:
    def __init__(self, lideres, falhar_leitura=False, falhar_gravacao_de=None, ids=None):
        self.lideres = lideres
        self.ids = ids if ids is not None else dict(IDS)
        self.gravados = []
        self.falhar_leitura = falhar_leitura
        self.falhar_gravacao_de = falhar_gravacao_de

    def find_user_by_email(self, email):
        i = self.ids.get(email)
        return {"id": i, "email": email, "active": True} if i else None

    def get_leader_ids(self, uid):
        if self.falhar_leitura:
            raise LeituraLiderancaIndisponivel("sem leader_ids")
        return list(self.lideres.get(uid, []))

    def set_leader_ids(self, uid, ids):
        if uid == self.falhar_gravacao_de:
            raise RuntimeError("UBD 500")
        self.gravados.append((uid, list(ids)))


def _orq_sync(monkeypatch, ubd, regioes=None):
    monkeypatch.setattr(settings, "validate_sap", lambda: True)
    monkeypatch.setattr(settings, "validate_learning_rocks", lambda: True)
    orq = AutomationFlowOrchestrator(dry_run=False, interactive=False)
    orq.sap = SapStub()
    orq.regioes = regioes or RegioesStub()
    orq.learning_rocks = ubd
    return orq


def _detalhes(res):
    assert [r.step_name for r in res] == [STEP_UBD_SYNC]
    return res[0]


# rep.um tem um líder manual (99) e o supervisor antigo marcos (21, do histórico)
LIDERES_INICIAIS = {10: [99, 21], 20: []}


def test_modo_relatorio_calcula_e_nao_grava(monkeypatch):
    ubd = UbdStub(dict(LIDERES_INICIAIS))
    etapa = _detalhes(_orq_sync(monkeypatch, ubd).run_leadership_sync_flow("relatorio", ["marcos@bondmann.com.br"]))
    assert etapa.status.value == "SUCCESS" and ubd.gravados == []
    d = etapa.details
    assert d["modo"] == "relatorio" and d["verificados"] == 2
    rep = next(c for c in d["corrigidos"] if c["email"] == "rep.um@bondmann.com.br")
    assert rep["adicionados"] == ["bruno@bondmann.com.br", "jorge@bondmann.com.br"]
    assert rep["removidos"] == ["marcos@bondmann.com.br"]
    assert d["lideres_comerciais"] == ["bruno@bondmann.com.br", "jorge@bondmann.com.br"]


def test_modo_aplicar_grava_lista_completa_e_preserva_manual(monkeypatch):
    ubd = UbdStub(dict(LIDERES_INICIAIS))
    _orq_sync(monkeypatch, ubd).run_leadership_sync_flow("aplicar", ["marcos@bondmann.com.br"])
    assert ubd.gravados == [(20, [30]), (10, [99, 20, 30])]


def test_regiao_divergente_nao_mexe_em_ninguem_dela(monkeypatch):
    ubd = UbdStub(dict(LIDERES_INICIAIS))
    regioes = RegioesStub({"001-A": {"email_supervisor": "outro@bondmann.com.br", "email_gerente": "bruno@bondmann.com.br"}})
    etapa = _detalhes(_orq_sync(monkeypatch, ubd, regioes).run_leadership_sync_flow("aplicar", []))
    assert ubd.gravados == [] and etapa.details["verificados"] == 0
    assert {p["email"] for p in etapa.details["divergentes"]} == {"rep.um@bondmann.com.br", "jorge@bondmann.com.br"}


def test_leitura_da_ubd_quebrada_falha_a_etapa(monkeypatch):
    ubd = UbdStub(dict(LIDERES_INICIAIS), falhar_leitura=True)
    etapa = _detalhes(_orq_sync(monkeypatch, ubd).run_leadership_sync_flow("aplicar", []))
    assert etapa.status.value == "FAILED" and ubd.gravados == []


def test_erro_num_usuario_nao_para_os_demais(monkeypatch):
    ubd = UbdStub(dict(LIDERES_INICIAIS), falhar_gravacao_de=20)
    etapa = _detalhes(_orq_sync(monkeypatch, ubd).run_leadership_sync_flow("aplicar", ["marcos@bondmann.com.br"]))
    assert etapa.status.value == "SUCCESS"
    assert [e["email"] for e in etapa.details["erros"]] == ["jorge@bondmann.com.br"]
    assert ubd.gravados == [(10, [99, 20, 30])]


def test_usuario_fora_da_ubd_vai_para_nao_encontrados(monkeypatch):
    ids = dict(IDS)
    del ids["rep.um@bondmann.com.br"]
    ubd = UbdStub(dict(LIDERES_INICIAIS), ids=ids)
    etapa = _detalhes(_orq_sync(monkeypatch, ubd).run_leadership_sync_flow("aplicar", []))
    assert etapa.details["nao_encontrados"] == ["rep.um@bondmann.com.br"]


def test_executar_job_de_sincronizacao_repassa_modo_e_gerenciados():
    import worker as w

    visto = {}

    class Orq:
        def __init__(self, **kw):
            pass

        def run_leadership_sync_flow(self, modo, gerenciados):
            visto.update(modo=modo, gerenciados=list(gerenciados))
            return [StepResult(success=True, step_name=STEP_UBD_SYNC, data={"verificados": 1})]

    corpo = w.executar_job({"tipo": "SINCRONIZAR_LIDERANCA", "dry_run": False,
                            "payload": {"modo": "aplicar", "lideres_gerenciados": ["a@bondmann.com.br"]}}, Orq)
    assert visto == {"modo": "aplicar", "gerenciados": ["a@bondmann.com.br"]}
    assert corpo["erro"] is None and corpo["etapas"][0]["status"] == "SUCCESS"
    w.executar_job({"tipo": "SINCRONIZAR_LIDERANCA", "dry_run": True, "payload": {"modo": "aplicar"}}, Orq)
    assert visto["modo"] == "relatorio"  # simulação nunca aplica
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sync_lideranca.py -q`
Expected: FAIL — `ImportError: cannot import name 'STEP_UBD_SYNC' from 'flow'`.

- [ ] **Step 3: Write minimal implementation** (preservando CRLF em `flow.py` e `worker.py`)

`flow.py`:
- `from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple`
- junto dos imports de serviços: `from services.learning_rocks import LeituraLiderancaIndisponivel` e `from services.lideranca import montar_alvos, nova_lista`
- junto das constantes de etapa: `STEP_UBD_SYNC = "UBD Learning.rocks - Sincronização de Liderança"`
- na classe, antes de `# Fluxo de criação`:

```python
    # ------------------------------------------------------------------
    # Sincronização de liderança (plano v2, F5)
    # ------------------------------------------------------------------
    def run_leadership_sync_flow(self, modo: str, lideres_gerenciados: Iterable[str]) -> List[StepResult]:
        """Etapa única. ``modo = "aplicar"`` grava; qualquer outro valor (ou
        dry-run) só calcula e relata."""
        self.abortado_em = None
        results: List[StepResult] = []
        aplicar = modo == "aplicar" and not self.dry_run
        gerenciados = [str(e) for e in lideres_gerenciados]
        self._run_step(results, STEP_UBD_SYNC, lambda: self._sincronizar_lideranca(aplicar, gerenciados))
        return results

    def _sincronizar_lideranca(self, aplicar: bool, lideres_gerenciados: List[str]) -> Dict[str, Any]:
        if not settings.validate_sap():
            raise RuntimeError("Credenciais SAP não configuradas — sincronização não roda")
        if not settings.validate_learning_rocks():
            raise RuntimeError("Token do Learning.rocks ausente — sincronização não roda")
        try:
            regioes, email_pn = self.sap.mapa_lideranca()
        finally:
            self.sap.logout()

        divergentes: Set[str] = set()
        nao_confirmadas = 0
        for r in regioes:
            code = r.get("Code") or ""
            sap = {
                "regiao": code,
                "supervisor": email_pn.get(r.get("U_IB_CodCom3") or ""),
                "gerente": email_pn.get(r.get("U_IB_CodCom4") or ""),
            }
            estado, _ = confirmar_lideres(sap, self.regioes.linha(code))
            if estado == "divergente":
                divergentes.add(code)
            elif estado == "nao_confirmado":
                nao_confirmadas += 1
        alvos, pulados, comerciais = montar_alvos(regioes, email_pn, divergentes)

        ids: Dict[str, Optional[int]] = {}

        def id_ubd(email: str) -> Optional[int]:
            email = email.strip().lower()
            if email not in ids:
                u = self.learning_rocks.find_user_by_email(email)
                ok = bool(u and u.get("id") and (u.get("email") or "").strip().lower() == email and u.get("active", True))
                ids[email] = int(u["id"]) if ok else None
            return ids[email]

        def email_de(uid: int) -> str:
            return next((e for e, i in ids.items() if i == uid), str(uid))

        gerenciados_ids = {i for e in set(lideres_gerenciados) | comerciais if (i := id_ubd(e)) is not None}
        corrigidos: List[Dict[str, Any]] = []
        nao_encontrados: List[str] = []
        erros: List[Dict[str, str]] = []
        avisos: Set[str] = set()
        for email in sorted(alvos):
            uid = id_ubd(email)
            if uid is None:
                nao_encontrados.append(email)
                continue
            try:
                atuais = self.learning_rocks.get_leader_ids(uid)
                esperados_ids: Set[int] = set()
                for lider in sorted(alvos[email].esperados):
                    lid = id_ubd(lider)
                    if lid is None:
                        avisos.add(f"líder {lider} não encontrado na UBD")
                    else:
                        esperados_ids.add(lid)
                novo = nova_lista(atuais, esperados_ids, gerenciados_ids)
                if set(novo) != set(atuais):
                    if aplicar:
                        self.learning_rocks.set_leader_ids(uid, novo)
                    corrigidos.append({
                        "email": email,
                        "adicionados": sorted(email_de(i) for i in set(novo) - set(atuais)),
                        "removidos": sorted(email_de(i) for i in set(atuais) - set(novo)),
                    })
            except LeituraLiderancaIndisponivel:
                raise
            except Exception as exc:  # noqa: BLE001 — um usuário não para os demais
                erros.append({"email": email, "erro": str(exc)[:300]})
        return {
            "modo": "aplicar" if aplicar else "relatorio",
            "verificados": len(alvos),
            "corrigidos": corrigidos,
            "nao_encontrados": nao_encontrados,
            "divergentes": pulados,
            "regioes_nao_confirmadas": nao_confirmadas,
            "erros": erros,
            "avisos": sorted(avisos),
            "lideres_comerciais": sorted(comerciais),
        }
```

`worker.py`:
- `TIPO_SINCRONIZAR_LIDERANCA = "SINCRONIZAR_LIDERANCA"` junto dos outros e `TIPOS = (TIPO_CRIACAO, TIPO_DESLIGAMENTO, TIPO_REVOGAR_LICENCA, TIPO_SINCRONIZAR_LIDERANCA)`
- em `executar_job`, antes do `else:` final:

```python
        elif tipo == TIPO_SINCRONIZAR_LIDERANCA:
            modo = "aplicar" if str(payload.get("modo") or "") == "aplicar" and not dry_run else "relatorio"
            gerenciados = [str(e) for e in (payload.get("lideres_gerenciados") or [])]
            steps = orch.run_leadership_sync_flow(modo, gerenciados)
            corpo["etapas"] = serializar_etapas(steps)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sync_lideranca.py tests/test_lideranca.py tests/test_vagas.py tests/test_worker.py tests/test_sap_usuarios.py tests/test_pn_fornecedor.py tests/test_wmw_cadastro.py -q`
Expected: PASS; `git diff --stat` sem diffs de arquivo inteiro (fim de linha preservado).

- [ ] **Step 5: Commit** — `git add flow.py worker.py tests/test_sync_lideranca.py`; mensagem `feat(flow): sincronizacao de lideranca na UBD - espelho com protecao (v2 F5)`.

---

## Portal (este repo)

Branch: `git checkout claude/develop && git pull --ff-only && git checkout -b feat/automacao-v2-f5-sync`.

### Task 5: Migrations 0092/0093 e RLS

**Files:**
- Create: `supabase/migrations/0092_automacao_tipo_sincronizar_lideranca.sql`, `supabase/migrations/0093_automacao_sync_lideranca.sql`
- Modify: `plano_md_mestre_automacao_acessos.md` (Seção 5 — protocolo: DDL documentado **antes** do código)
- Test: `tests/e2e/test_rls_automacao_sync.py` (novo, marker `rls`)

**Interfaces:**
- Produces: enum `automacao_tipo` com `SINCRONIZAR_LIDERANCA`; `automacao_jobs.chamado_id`/`aprovado_por` NULL só para sync (`ck_automacao_jobs_origem`); `ux_automacao_jobs_sync_ativo`; tabela `automacao_lideranca_gerenciada(email text PK, primeiro_visto, ultimo_visto)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_rls_automacao_sync.py
"""e2e da sincronização de liderança (migrations 0092 + 0093, marker ``rls``).

Sync não tem chamado: as policies da 0091 (que exigem chamado do setor) devem
escondê-la do staff e impedir que a tela crie uma; o CHECK garante que só a
sync fica sem chamado; o índice garante uma sync ativa por vez."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import asyncpg
import pytest

from tests.e2e.conftest import Seed, as_user

pytestmark = pytest.mark.rls


async def _aplicada(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass('public.automacao_lideranca_gerenciada') IS NOT NULL"))


@asynccontextmanager
async def savepoint(conn: asyncpg.Connection):
    tx = conn.transaction()
    await tx.start()
    try:
        yield
    finally:
        await tx.rollback()


async def _sync(conn: asyncpg.Connection) -> str:
    return await conn.fetchval(
        "INSERT INTO automacao_jobs (tipo, payload) VALUES ('SINCRONIZAR_LIDERANCA', $1::jsonb) RETURNING id",
        json.dumps({"modo": "relatorio"}),
    )


async def test_sync_sem_chamado_e_invisivel_para_o_staff(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    job_id = await _sync(conn)
    async with as_user(conn, seed.staff_ti) as c:
        assert await c.fetchval("SELECT count(*) FROM automacao_jobs WHERE id = $1", job_id) == 0


async def test_staff_nao_cria_sync_pela_tela(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    async with as_user(conn, seed.staff_ti) as c:
        async with savepoint(c):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(
                    "INSERT INTO automacao_jobs (tipo, payload, aprovado_por) VALUES ('SINCRONIZAR_LIDERANCA', '{}'::jsonb, $1)",
                    seed.staff_ti,
                )


async def test_so_a_sync_fica_sem_chamado(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    async with savepoint(conn):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("INSERT INTO automacao_jobs (tipo, payload) VALUES ('CRIACAO', '{}'::jsonb)")


async def test_uma_sync_ativa_por_vez(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    await _sync(conn)
    async with savepoint(conn):
        with pytest.raises(asyncpg.UniqueViolationError):
            await _sync(conn)


async def test_gerenciados_so_pela_conexao_administrativa(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    async with as_user(conn, seed.staff_ti) as c:
        async with savepoint(c):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.fetch("SELECT email FROM automacao_lideranca_gerenciada")
```

- [ ] **Step 2: Run test to verify it fails**

Run (com o Supabase local e `RLS_DATABASE_URL` como no `tests/e2e/README.md`): `python -m pytest tests/e2e/test_rls_automacao_sync.py -m rls -o addopts=""`
Expected: os 5 testes `SKIPPED` ("0093 não aplicada") — sem as migrations não há o que testar. (Sem `RLS_DATABASE_URL` a suíte inteira é pulada; o CI `E2E RLS` roda com banco.)

- [ ] **Step 3: Write minimal implementation**

`supabase/migrations/0092_automacao_tipo_sincronizar_lideranca.sql`:

```sql
-- 0092_automacao_tipo_sincronizar_lideranca.sql
-- Automação de acessos v2, F5 (plano_md_mestre_automacao_acessos_v2.md, Seção 6).
-- Sozinha de propósito: o valor novo de um enum não pode ser USADO na mesma
-- transação em que é criado — a 0093 (CHECK e índice) depende dele.
ALTER TYPE automacao_tipo ADD VALUE IF NOT EXISTS 'SINCRONIZAR_LIDERANCA';
```

`supabase/migrations/0093_automacao_sync_lideranca.sql`:

```sql
-- 0093_automacao_sync_lideranca.sql
-- Sincronização de liderança na UBD (v2, F5). A sync não pertence a um chamado:
-- nasce da vigilância (06h) ou do resultado de um job de supervisor/gerente, e
-- só a conexão administrativa a cria. As policies da 0091 exigem um chamado do
-- setor, então a sync fica invisível para o staff e a tela não consegue criar
-- uma — sem policy nova. O relatório vai por nota/e-mail.
BEGIN;

ALTER TABLE automacao_jobs ALTER COLUMN chamado_id DROP NOT NULL;
ALTER TABLE automacao_jobs ALTER COLUMN aprovado_por DROP NOT NULL;

ALTER TABLE automacao_jobs DROP CONSTRAINT IF EXISTS ck_automacao_jobs_origem;
ALTER TABLE automacao_jobs ADD CONSTRAINT ck_automacao_jobs_origem CHECK (
  tipo = 'SINCRONIZAR_LIDERANCA' OR (chamado_id IS NOT NULL AND aprovado_por IS NOT NULL)
);

-- Uma sincronização na fila ou rodando por vez (a de evento e a diária se somam nela).
CREATE UNIQUE INDEX IF NOT EXISTS ux_automacao_jobs_sync_ativo
  ON automacao_jobs (tipo)
  WHERE tipo = 'SINCRONIZAR_LIDERANCA' AND status IN ('NA_FILA', 'EXECUTANDO');

-- Supervisores/gerentes que a automação já viu no SAP: a sync só remove da UBD
-- líderes desta lista (espelho com proteção, V4) — os de hoje E os de antes,
-- para um supervisor desligado sair dos representantes.
CREATE TABLE IF NOT EXISTS automacao_lideranca_gerenciada (
  email          text PRIMARY KEY CHECK (email = lower(email)),
  primeiro_visto timestamptz NOT NULL DEFAULT now(),
  ultimo_visto   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE automacao_lideranca_gerenciada IS
  'Líderes comerciais (supervisor/gerente) já vistos pela sincronização de liderança (plano v2, F5). Só admin_connection().';
ALTER TABLE automacao_lideranca_gerenciada ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON automacao_lideranca_gerenciada FROM anon, authenticated;

COMMIT;
```

`plano_md_mestre_automacao_acessos.md`, fim da Seção 5, acrescentar:

```markdown
**v2 F5 — migrations 0092/0093 (sincronização de liderança).** `automacao_tipo` ganha
`SINCRONIZAR_LIDERANCA` (0092, sozinha). Na 0093, `chamado_id` e `aprovado_por` passam a
aceitar NULL **só** para esse tipo (`ck_automacao_jobs_origem`), há no máximo uma
sincronização ativa (`ux_automacao_jobs_sync_ativo`) e nasce `automacao_lideranca_gerenciada`
(e-mails de supervisores/gerentes já vistos no SAP; RLS ligada, sem grants — só
`admin_connection()`). As policies da 0091 não mudam: como exigem um chamado do setor, a
sincronização fica invisível para o staff e não pode ser criada pela tela.
```

- [ ] **Step 4: Run test to verify it passes**

Aplicar as duas migrations no Supabase **local** (`supabase db reset` ou `psql "$RLS_DATABASE_URL" -f …0092… -f …0093…`) e rodar:
Run: `python -m pytest tests/e2e/test_rls_automacao_sync.py tests/e2e/test_rls_matrix.py -m rls -o addopts=""`
Expected: 5 passed (sync) + matriz verde.

- [ ] **Step 5: Commit** — `git add supabase/migrations/0092_automacao_tipo_sincronizar_lideranca.sql supabase/migrations/0093_automacao_sync_lideranca.sql tests/e2e/test_rls_automacao_sync.py plano_md_mestre_automacao_acessos.md`; mensagem `feat(db): tipo SINCRONIZAR_LIDERANCA, sync sem chamado e lideres gerenciados (v2 F5)`.

### Task 6: Domínio e configuração da sincronização

**Files:**
- Modify: `app/domain/automacao.py`, `app/config.py`
- Test: `tests/test_automacao.py`

**Interfaces:**
- Produces (em `app/domain/automacao.py`): `TIPO_SINCRONIZAR_LIDERANCA`, `TIPOS` com o tipo novo, `SYNC_MODO_RELATORIO = "relatorio"`, `SYNC_MODO_APLICAR = "aplicar"`, `ETAPA_SYNC`, `ETAPAS_VAGA`, `montar_payload_sync(modo, lideres_gerenciados, origem=None) -> dict`, `dispara_sync_por_evento(job, etapas) -> bool`, `detalhes_sync(etapas) -> dict`, `sync_diaria_devida(agora, hora, ultima_criacao) -> bool`, `sync_precisa_alerta(status_final, detalhes) -> bool`, `texto_relatorio_sync(job, etapas, status_final, *, erro_geral=None) -> str`.
- Produces (em `Settings`): `automacao_sync_modo: str = "relatorio"`, `automacao_sync_hora: int = 6`, `automacao_sync_atraso_min: int = 60`.

- [ ] **Step 1: Write the failing test** (em `tests/test_automacao.py`)

```python
# --------------------------------------------------------------------------
# Sincronização de liderança (v2, F5)
# --------------------------------------------------------------------------
ETAPA_OCUPAR = "SAP Business One (Service Layer) - Ocupar Vaga (Equipe/Gerência)"


def test_payload_sync_normaliza_modo_e_emails():
    p = dom.montar_payload_sync("APLICAR", [" B@bondmann.com.br", "a@bondmann.com.br", ""], {"id": "c1", "codigo": "BD-1"})
    assert p["tipo"] == "SINCRONIZAR_LIDERANCA" and p["modo"] == "aplicar"
    assert p["lideres_gerenciados"] == ["a@bondmann.com.br", "b@bondmann.com.br"]
    assert p["chamado"] == {"id": "c1", "codigo": "BD-1"} and p["pular_etapas"] == []
    assert dom.montar_payload_sync("qualquer coisa", [])["modo"] == "relatorio"
    assert dom.montar_payload_sync("aplicar", [])["chamado"] is None


def test_sync_por_evento_so_para_vaga_real_de_supervisor_ou_gerente():
    etapas = [{"nome": ETAPA_OCUPAR, "status": "SUCCESS"}]
    job = {"tipo": "CRIACAO", "dry_run": False, "payload": {"perfil": "SUPERVISOR"}}
    assert dom.dispara_sync_por_evento(job, etapas)
    assert not dom.dispara_sync_por_evento({**job, "dry_run": True}, etapas)
    assert not dom.dispara_sync_por_evento({**job, "payload": {"perfil": "REPRESENTANTE"}}, etapas)
    assert not dom.dispara_sync_por_evento(job, [{"nome": ETAPA_OCUPAR, "status": "FAILED"}])
    assert not dom.dispara_sync_por_evento({**job, "tipo": "REVOGAR_LICENCA"}, etapas)


def test_sync_diaria_a_partir_das_6h_uma_vez_por_dia():
    # 2026-09-30 09:00 UTC = 06:00 em Brasília
    seis = datetime(2026, 9, 30, 9, 0, tzinfo=UTC)
    assert not dom.sync_diaria_devida(seis - timedelta(minutes=1), 6, None)
    assert dom.sync_diaria_devida(seis, 6, None)
    assert dom.sync_diaria_devida(seis, 6, datetime(2026, 9, 29, 9, 30, tzinfo=UTC))
    assert not dom.sync_diaria_devida(seis, 6, datetime(2026, 9, 30, 3, 5, tzinfo=UTC))  # 00:05 BR do mesmo dia


def test_alerta_da_sync_sempre_no_relatorio_e_so_com_problema_no_aplicar():
    limpo = {"modo": "aplicar", "nao_encontrados": [], "divergentes": [], "erros": []}
    assert dom.sync_precisa_alerta("CONCLUIDO", {"modo": "relatorio"})
    assert not dom.sync_precisa_alerta("CONCLUIDO", limpo)
    assert dom.sync_precisa_alerta("CONCLUIDO", {**limpo, "erros": [{"email": "x", "erro": "y"}]})
    assert dom.sync_precisa_alerta("FALHOU", limpo)


def test_texto_do_relatorio_da_sync():
    etapas = [{"nome": dom.ETAPA_SYNC, "status": "SUCCESS", "erro": None, "detalhes": {
        "modo": "relatorio", "verificados": 3, "regioes_nao_confirmadas": 1,
        "corrigidos": [{"email": "rep@bondmann.com.br", "adicionados": ["sup@bondmann.com.br"], "removidos": ["velho@bondmann.com.br"]}],
        "nao_encontrados": ["fulano@bondmann.com.br"], "divergentes": [], "erros": [], "avisos": []}}]
    txt = dom.texto_relatorio_sync({"worker_id": "w1", "payload": {"chamado": {"id": "c1", "codigo": "BD-9"}}}, etapas, "CONCLUIDO")
    assert "MODO RELATÓRIO" in txt and "BD-9" in txt and "Usuários verificados: 3" in txt
    assert "rep@bondmann.com.br: + sup@bondmann.com.br; − velho@bondmann.com.br" in txt
    assert "fulano@bondmann.com.br" in txt
```

(`timedelta` já é importado no topo de `tests/test_automacao.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_automacao.py -p no:cacheprovider -o addopts="" -q -k "sync"`
Expected: FAIL — `AttributeError: module 'app.domain.automacao' has no attribute 'montar_payload_sync'`.

- [ ] **Step 3: Write minimal implementation**

`app/config.py`, logo depois de `automacao_hora_desligamento`:

```python
    # Sincronização de liderança na UBD (plano v2, F5): "relatorio" só relata;
    # "aplicar" grava. Diária a partir de `automacao_sync_hora` (Brasília) e por
    # evento `automacao_sync_atraso_min` depois de mexer numa vaga (a `regioes`
    # se atualiza em lote).
    automacao_sync_modo: str = Field(default="relatorio")
    automacao_sync_hora: int = Field(default=6)
    automacao_sync_atraso_min: int = Field(default=60)
```

`app/domain/automacao.py`:
- `TIPO_SINCRONIZAR_LIDERANCA = "SINCRONIZAR_LIDERANCA"` e `TIPOS = (TIPO_CRIACAO, TIPO_DESLIGAMENTO, TIPO_REVOGAR_LICENCA, TIPO_SINCRONIZAR_LIDERANCA)`
- em `_TIPO_LABEL` e `_TIPO_FRASE`, as entradas do tipo novo: `TIPO_SINCRONIZAR_LIDERANCA: "sincronização de liderança"` e `TIPO_SINCRONIZAR_LIDERANCA: ("A", "sincronização de liderança", "concluída", "executada")`
- ao final do módulo:

```python
# --------------------------------------------------------------------------
# Sincronização de liderança na UBD (plano v2, F5)
# --------------------------------------------------------------------------
SYNC_MODO_RELATORIO = "relatorio"
SYNC_MODO_APLICAR = "aplicar"
ETAPA_SYNC = "UBD Learning.rocks - Sincronização de Liderança"
ETAPAS_VAGA = (
    "SAP Business One (Service Layer) - Ocupar Vaga (Equipe/Gerência)",
    "SAP Business One (Service Layer) - Devolver Vaga (Equipe/Gerência)",
)
_PERFIS_COM_VAGA = ("SUPERVISOR", "GERENTE")


def montar_payload_sync(
    modo: str, lideres_gerenciados: Any, origem: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Payload do job SINCRONIZAR_LIDERANCA — sem chamado próprio; ``origem`` é o
    chamado que disparou (evento), só para a nota interna do relatório."""
    return {
        "versao": VERSAO_CONTRATO,
        "tipo": TIPO_SINCRONIZAR_LIDERANCA,
        "chamado": ({"id": str(origem["id"]), "codigo": origem.get("codigo") or ""} if origem else None),
        "pular_etapas": [],
        "modo": SYNC_MODO_APLICAR if str(modo or "").strip().lower() == SYNC_MODO_APLICAR else SYNC_MODO_RELATORIO,
        "lideres_gerenciados": sorted({str(e).strip().lower() for e in (lideres_gerenciados or []) if str(e).strip()}),
    }


def dispara_sync_por_evento(job: dict[str, Any], etapas: list[dict[str, Any]]) -> bool:
    """Criação/desligamento REAL de supervisor/gerente que mexeu na vaga no SAP."""
    if job.get("dry_run") or str(job.get("tipo")) not in (TIPO_CRIACAO, TIPO_DESLIGAMENTO):
        return False
    if (job.get("payload") or {}).get("perfil") not in _PERFIS_COM_VAGA:
        return False
    return any(e.get("nome") in ETAPAS_VAGA and e.get("status") == ETAPA_SUCCESS for e in etapas)


def detalhes_sync(etapas: list[dict[str, Any]]) -> dict[str, Any]:
    for e in etapas:
        if e.get("nome") == ETAPA_SYNC:
            return e.get("detalhes") or {}
    return {}


def sync_diaria_devida(agora: datetime, hora: int, ultima_criacao: datetime | None) -> bool:
    """A partir de ``hora`` (Brasília), se nenhuma sync foi criada hoje."""
    local = agora.astimezone(TZ_BR)
    if local.hour < hora:
        return False
    return ultima_criacao is None or ultima_criacao.astimezone(TZ_BR).date() < local.date()


def sync_precisa_alerta(status_final: str, detalhes: dict[str, Any]) -> bool:
    """Modo relatório: sempre (o gestor revisa). Aplicar: só com problema."""
    if detalhes.get("modo") != SYNC_MODO_APLICAR or status_final != STATUS_CONCLUIDO:
        return True
    return any(detalhes.get(k) for k in ("nao_encontrados", "divergentes", "erros"))


def texto_relatorio_sync(
    job: dict[str, Any], etapas: list[dict[str, Any]], status_final: str, *, erro_geral: str | None = None
) -> str:
    """Relatório da sincronização (nota interna do chamado de origem e e-mail à TI)."""
    d = detalhes_sync(etapas)
    modo = d.get("modo") or (job.get("payload") or {}).get("modo") or SYNC_MODO_RELATORIO
    cab = "Sincronização de liderança na UBD — " + (
        "aplicada" if modo == SYNC_MODO_APLICAR else "MODO RELATÓRIO (nada foi alterado na UBD)"
    )
    partes = [cab, f"Status: {STATUS_LABEL.get(status_final, status_final)}", f"Worker: {job.get('worker_id') or '—'}"]
    origem = (job.get("payload") or {}).get("chamado") or {}
    if origem.get("codigo"):
        partes.append(f"Disparada pela troca de vaga no chamado {origem['codigo']}")
    if erro_geral:
        partes += ["", f"Erro fora do fluxo: {erro_geral}"]
    for e in etapas:
        if e.get("status") != ETAPA_SUCCESS and e.get("erro"):
            partes += ["", f"Etapa com falha: {e.get('erro')}"]
    partes += [
        "",
        f"Usuários verificados: {d.get('verificados', 0)}",
        f"Regiões não confirmadas na regioes: {d.get('regioes_nao_confirmadas', 0)}",
    ]
    corrigidos = d.get("corrigidos") or []
    rotulo = "Corrigidos" if modo == SYNC_MODO_APLICAR else "Seriam corrigidos"
    partes += ["", f"{rotulo} ({len(corrigidos)}):"]
    for c in corrigidos:
        mais = ", ".join(c.get("adicionados") or []) or "—"
        menos = ", ".join(c.get("removidos") or []) or "—"
        partes.append(f"- {c.get('email')}: + {mais}; − {menos}")
    for chave, titulo in (("nao_encontrados", "Fora da UBD (ou inativos)"), ("avisos", "Avisos")):
        itens = d.get(chave) or []
        if itens:
            partes += ["", f"{titulo} ({len(itens)}):"] + [f"- {i}" for i in itens]
    divergentes = d.get("divergentes") or []
    if divergentes:
        partes += ["", f"Fora do cálculo — região diverge entre SAP e regioes ({len(divergentes)}):"]
        partes += [f"- {p.get('email')} (regiões {p.get('regioes')})" for p in divergentes]
    erros = d.get("erros") or []
    if erros:
        partes += ["", f"Erros ({len(erros)}):"] + [f"- {x.get('email')}: {x.get('erro')}" for x in erros]
    return "\n".join(partes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_automacao.py -p no:cacheprovider -o addopts="" -q` e `ruff check app tests`
Expected: PASS; ruff limpo.

- [ ] **Step 5: Commit** — `git add app/domain/automacao.py app/config.py tests/test_automacao.py`; mensagem `feat(automacao): dominio da sincronizacao de lideranca (v2 F5)`.

### Task 7: Repositório — fila sem chamado obrigatório e líderes gerenciados

**Files:**
- Modify: `app/repositories/automacao.py`, `app/routes/automacao_api.py` (`_job_publico`)
- Test: `tests/e2e/test_rls_automacao_sync.py` (SQL real), `tests/test_automacao.py` (API)

**Interfaces:**
- Consumes: migrations da Task 5.
- Produces: `admin_claim_proximo`, `admin_finalizar`, `admin_jobs_travados`, `admin_marcar_travado` aceitando job sem chamado (`chamado_codigo` etc. = `None`); `admin_agendar_job(*, chamado_id: str | None, tipo, payload, executar_apos, aprovado_por: str | None, reexecucao_de=None)`; `admin_ultima_sync_criada_em() -> datetime | None`; `admin_lideres_gerenciados() -> list[str]`; `admin_registrar_lideres_comerciais(emails: list[str]) -> None`; `_job_publico` com `"chamado": None` para job sem chamado.

- [ ] **Step 1: Write the failing test**

Em `tests/e2e/test_rls_automacao_sync.py`:

```python
from app.repositories import automacao as repo_admin  # noqa: E402


@pytest.fixture
def admin_na_transacao(conn, monkeypatch):
    """`admin_*` rodando na conexão do teste (mesma transação, rollback no fim)."""

    @asynccontextmanager
    async def _fixa():
        yield conn

    monkeypatch.setattr(repo_admin, "admin_connection", _fixa)
    return conn


async def test_fila_entrega_finaliza_e_trava_sync_sem_chamado(admin_na_transacao, seed: Seed):
    conn = admin_na_transacao
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    await conn.execute("UPDATE automacao_jobs SET status = 'CANCELADO' WHERE status IN ('NA_FILA', 'EXECUTANDO')")
    novo = await repo_admin.admin_agendar_job(
        chamado_id=None, tipo="SINCRONIZAR_LIDERANCA", payload={"modo": "relatorio"},
        executar_apos=repo_admin.agora_utc(), aprovado_por=None,
    )
    assert novo is not None and novo["chamado_id"] is None
    job = await repo_admin.admin_claim_proximo("w-e2e", ["SINCRONIZAR_LIDERANCA"])
    assert job["id"] == novo["id"] and job["chamado_codigo"] is None
    await conn.execute("UPDATE automacao_jobs SET heartbeat_at = now() - interval '2 hours' WHERE id = $1", job["id"])
    assert [j["id"] for j in await repo_admin.admin_jobs_travados(60)] == [job["id"]]
    fim = await repo_admin.admin_finalizar(str(job["id"]), "w-e2e", status="CONCLUIDO", resultado=[], erro=None)
    assert fim["status"] == "CONCLUIDO" and fim["chamado_codigo"] is None
    assert await repo_admin.admin_ultima_sync_criada_em() is not None


async def test_lideres_gerenciados_acumulam(admin_na_transacao, seed: Seed):
    conn = admin_na_transacao
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    await repo_admin.admin_registrar_lideres_comerciais(["Sup.Um@bondmann.com.br", "ger@bondmann.com.br"])
    await repo_admin.admin_registrar_lideres_comerciais(["ger@bondmann.com.br"])
    emails = await repo_admin.admin_lideres_gerenciados()
    assert {"sup.um@bondmann.com.br", "ger@bondmann.com.br"} <= set(emails)
```

Em `tests/test_automacao.py`:

```python
def test_api_entrega_sync_sem_chamado(settings_automacao, monkeypatch):
    async def _claim(worker_id, tipos):
        return {"id": "s1", "tipo": "SINCRONIZAR_LIDERANCA", "dry_run": False, "tentativas": 1,
                "chamado_id": None, "chamado_codigo": None, "payload": {"modo": "relatorio"}}

    monkeypatch.setattr(settings_automacao, "automacao_tipos", "SINCRONIZAR_LIDERANCA")
    monkeypatch.setattr(repo_admin, "admin_claim_proximo", _claim)
    with api_client() as c:
        r = c.post("/api/automacao/jobs/proximo", headers=_h())
    assert r.status_code == 200 and r.json()["chamado"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_automacao.py -p no:cacheprovider -o addopts="" -q -k sync_sem_chamado` (e, com o banco local, `python -m pytest tests/e2e/test_rls_automacao_sync.py -m rls -o addopts=""`)
Expected: FAIL — `TypeError: 'NoneType' ...` em `_job_publico` (`str(job["chamado_id"])` vira `"None"`) / e2e: claim devolve `None` (o `JOIN chamados` descarta a sync).

- [ ] **Step 3: Write minimal implementation**

`app/repositories/automacao.py` — trocar os `UPDATE … FROM chamados` por CTE + `LEFT JOIN`, e o `JOIN` de `admin_jobs_travados` por `LEFT JOIN`:

```python
async def admin_claim_proximo(worker_id: str, tipos: list[str]) -> dict[str, Any] | None:
    """Pega UM job vencido da fila, atomicamente (``FOR UPDATE SKIP LOCKED``):
    dois workers nunca levam o mesmo job. Job sem chamado (sincronização de
    liderança, v2 F5) sai com ``chamado_codigo = None``."""
    if not tipos:
        return None
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            f"""WITH proximo AS (
                    SELECT id FROM automacao_jobs
                     WHERE status = 'NA_FILA'
                       AND executar_apos <= now()
                       AND tipo::text = ANY($2::text[])
                     ORDER BY executar_apos, created_at
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                ), pego AS (
                    UPDATE automacao_jobs a
                       SET status = 'EXECUTANDO', worker_id = $1, claimed_at = now(),
                           heartbeat_at = now(), tentativas = tentativas + 1, etapa_atual = NULL
                      FROM proximo
                     WHERE a.id = proximo.id
                 RETURNING a.*
                )
                SELECT {_COLUNAS}, c.codigo AS chamado_codigo
                  FROM pego j LEFT JOIN chamados c ON c.id = j.chamado_id""",
            worker_id,
            tipos,
        )
    return _row(row)
```

```python
async def admin_finalizar(
    job_id: str,
    worker_id: str,
    *,
    status: str,
    resultado: list[dict[str, Any]],
    erro: str | None,
) -> dict[str, Any] | None:
    """Transição final vinda do worker. Só aceita se o job está EXECUTANDO
    com este ``worker_id`` (idempotência: um segundo POST de resultado não
    faz nada e devolve ``None``)."""
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            f"""WITH fim AS (
                    UPDATE automacao_jobs
                       SET status = $3::automacao_status, resultado = $4::jsonb, erro = $5,
                           finalizado_em = now(), heartbeat_at = now(), etapa_atual = NULL
                     WHERE id = $1::uuid AND worker_id = $2 AND status = 'EXECUTANDO'
                 RETURNING *
                )
                SELECT {_COLUNAS}, c.codigo AS chamado_codigo, c.titulo AS chamado_titulo,
                       c.cliente_id AS chamado_cliente_id, c.operador_id AS chamado_operador_id,
                       c.status::text AS chamado_status, c.departamento_id AS chamado_departamento_id
                  FROM fim j LEFT JOIN chamados c ON c.id = j.chamado_id""",
            job_id,
            worker_id,
            status,
            json.dumps(resultado),
            (erro or "")[:2000] or None,
        )
    return _row(row)
```

Em `admin_jobs_travados`: `FROM automacao_jobs j LEFT JOIN chamados c ON c.id = j.chamado_id`.

```python
async def admin_marcar_travado(job_id: str, motivo: str, *, requeue: bool) -> dict[str, Any] | None:
    """Vigilância: job morto vira FALHOU — ou volta pra fila (``requeue``)
    mantendo ``tentativas`` (o claim incrementa de novo)."""
    if requeue:
        mudanca = "status = 'NA_FILA', worker_id = NULL, claimed_at = NULL, heartbeat_at = NULL, etapa_atual = NULL, erro = $2"
    else:
        mudanca = "status = 'FALHOU', erro = $2, finalizado_em = now(), etapa_atual = NULL"
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            f"""WITH marcado AS (
                    UPDATE automacao_jobs SET {mudanca}
                     WHERE id = $1::uuid AND status = 'EXECUTANDO'
                 RETURNING *
                )
                SELECT {_COLUNAS}, c.codigo AS chamado_codigo
                  FROM marcado j LEFT JOIN chamados c ON c.id = j.chamado_id""",
            job_id,
            motivo,
        )
    return _row(row)
```

Em `admin_agendar_job`: assinatura `chamado_id: str | None`, `aprovado_por: str | None` e docstring "``chamado_id``/``aprovado_por`` só ficam vazios na sincronização de liderança (CHECK da 0093)". O corpo não muda.

Funções novas, no fim da seção administrativa:

```python
async def admin_ultima_sync_criada_em() -> datetime | None:
    """Quando a última sincronização de liderança foi enfileirada (gatilho diário)."""
    async with admin_connection() as conn:
        return await conn.fetchval(
            "SELECT max(created_at) FROM automacao_jobs WHERE tipo = 'SINCRONIZAR_LIDERANCA'"
        )


async def admin_lideres_gerenciados() -> list[str]:
    async with admin_connection() as conn:
        rows = await conn.fetch("SELECT email FROM automacao_lideranca_gerenciada ORDER BY email")
    return [r["email"] for r in rows]


async def admin_registrar_lideres_comerciais(emails: list[str]) -> None:
    """Supervisores/gerentes vistos no SAP pela sync: entram (ou renovam
    ``ultimo_visto``) na lista que a sync pode remover da UBD (V4)."""
    limpos = sorted({e.strip().lower() for e in emails if e and e.strip()})
    if not limpos:
        return
    async with admin_connection() as conn:
        await conn.execute(
            """INSERT INTO automacao_lideranca_gerenciada (email)
               SELECT unnest($1::text[])
               ON CONFLICT (email) DO UPDATE SET ultimo_visto = now()""",
            limpos,
        )
```

`app/routes/automacao_api.py`, em `_job_publico`:

```python
        "chamado": (
            {"id": str(job["chamado_id"]), "codigo": job.get("chamado_codigo") or ""}
            if job.get("chamado_id") else None
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_automacao.py -p no:cacheprovider -o addopts="" -q`, a e2e com o banco local (`tests/e2e/test_rls_automacao_sync.py`) e `ruff check app tests`
Expected: PASS (7 e2e de sync), ruff limpo.

- [ ] **Step 5: Commit** — `git add app/repositories/automacao.py app/routes/automacao_api.py tests/test_automacao.py tests/e2e/test_rls_automacao_sync.py`; mensagem `feat(automacao): fila aceita sync sem chamado; lideres gerenciados (v2 F5)`.

### Task 8: Serviço — gatilhos e processamento do resultado da sync

**Files:**
- Modify: `app/services/automacao.py`
- Test: `tests/test_automacao.py`

**Interfaces:**
- Consumes: Tasks 6 e 7.
- Produces: `agendar_sincronizacao(settings, *, executar_apos, origem_job=None) -> dict | None`; `processar_resultado_sync(job, etapas, *, settings) -> None`; `processar_resultado` desvia a sync e dispara a sync por evento; `vigiar_uma_vez` faz o gatilho diário e trata job travado sem chamado.

- [ ] **Step 1: Write the failing test** (em `tests/test_automacao.py`)

```python
class _AdminSync(_Admin):
    """_Admin + o que a sync usa (gerenciados, última sync, e-mails de alerta)."""

    def __init__(self, monkeypatch, *, gerenciados=(), ultima=None):
        super().__init__(monkeypatch)
        self.registrados: list[list[str]] = []
        self.alertas: list[tuple[str, str]] = []

        async def lideres():
            return list(gerenciados)

        async def registrar(emails):
            self.registrados.append(list(emails))

        async def ultima_sync():
            return ultima

        async def alerta(settings, assunto, corpo):
            self.alertas.append((assunto, corpo))

        monkeypatch.setattr(repo_admin, "admin_lideres_gerenciados", lideres)
        monkeypatch.setattr(repo_admin, "admin_registrar_lideres_comerciais", registrar)
        monkeypatch.setattr(repo_admin, "admin_ultima_sync_criada_em", ultima_sync)
        monkeypatch.setattr(svc, "_email_alerta_ti", alerta)


def _etapa_sync(**detalhes):
    base = {"modo": "relatorio", "verificados": 1, "corrigidos": [], "nao_encontrados": [], "divergentes": [],
            "erros": [], "avisos": [], "regioes_nao_confirmadas": 0, "lideres_comerciais": ["sup@bondmann.com.br"]}
    base.update(detalhes)
    return [{"nome": dom.ETAPA_SYNC, "status": "SUCCESS", "erro": None, "detalhes": base}]


def _job_sync(**over):
    base = {"id": "s1", "chamado_id": None, "tipo": "SINCRONIZAR_LIDERANCA", "status": "CONCLUIDO",
            "dry_run": False, "aprovado_por": None, "worker_id": "w1", "erro": None,
            "payload": dom.montar_payload_sync("relatorio", [])}
    base.update(over)
    return base


def test_resultado_da_sync_registra_comerciais_e_manda_relatorio(settings_automacao, monkeypatch):
    adm = _AdminSync(monkeypatch)
    _run(svc.processar_resultado(_job_sync(), _etapa_sync(), None, None, settings=settings_automacao))
    assert adm.registrados == [["sup@bondmann.com.br"]]
    assert len(adm.alertas) == 1 and "MODO RELATÓRIO" in adm.alertas[0][1]
    assert adm.mensagens == [] and adm.resolvidos == []  # sem chamado de origem: nada no portal


def test_sync_aplicada_limpa_nao_manda_email_mas_anota_no_chamado_de_origem(settings_automacao, monkeypatch):
    adm = _AdminSync(monkeypatch)
    job = _job_sync(aprovado_por=OP, payload=dom.montar_payload_sync("aplicar", [], {"id": "c9", "codigo": "BD-9"}))
    _run(svc.processar_resultado(job, _etapa_sync(modo="aplicar"), None, None, settings=settings_automacao))
    assert adm.alertas == []
    assert [(m[0], m[3]) for m in adm.mensagens] == [("c9", True)]


def test_vaga_real_de_supervisor_agenda_sync_com_atraso(settings_automacao, monkeypatch):
    adm = _AdminSync(monkeypatch, gerenciados=["velho@bondmann.com.br"])
    monkeypatch.setattr(settings_automacao, "automacao_tipos", "CRIACAO,SINCRONIZAR_LIDERANCA")
    monkeypatch.setattr(settings_automacao, "automacao_sync_modo", "aplicar")
    etapas = [{"nome": dom.ETAPAS_VAGA[0], "status": "SUCCESS", "erro": None, "detalhes": {}}]
    job = _job(status="CONCLUIDO_COM_PENDENCIAS", payload={"perfil": "SUPERVISOR", "email": "s@bondmann.com.br"})
    antes = datetime.now(UTC)
    _run(svc.processar_resultado(job, etapas, None, None, settings=settings_automacao))
    sync = [a for a in adm.agendados if a["tipo"] == "SINCRONIZAR_LIDERANCA"]
    assert len(sync) == 1 and sync[0]["chamado_id"] is None and sync[0]["aprovado_por"] == OP
    assert sync[0]["payload"]["modo"] == "aplicar" and sync[0]["payload"]["chamado"]["codigo"] == "BD-1"
    assert sync[0]["payload"]["lideres_gerenciados"] == ["velho@bondmann.com.br"]
    assert sync[0]["executar_apos"] >= antes + timedelta(minutes=59)


def test_sync_nao_e_agendada_sem_o_tipo_liberado(settings_automacao, monkeypatch):
    adm = _AdminSync(monkeypatch)
    etapas = [{"nome": dom.ETAPAS_VAGA[1], "status": "SUCCESS", "erro": None, "detalhes": {}}]
    job = _job(tipo="DESLIGAMENTO", payload={"perfil": "GERENTE", "email": "g@bondmann.com.br"})
    _run(svc.processar_resultado(job, etapas, None, None, settings=settings_automacao))
    assert not [a for a in adm.agendados if a["tipo"] == "SINCRONIZAR_LIDERANCA"]


def test_vigilancia_agenda_a_sync_diaria(settings_automacao, monkeypatch):
    adm = _AdminSync(monkeypatch, ultima=None)
    monkeypatch.setattr(settings_automacao, "automacao_tipos", "SINCRONIZAR_LIDERANCA")
    monkeypatch.setattr(settings_automacao, "automacao_sync_hora", 0)

    async def nada(*a, **k):
        return []

    async def vencida():
        return None

    monkeypatch.setattr(repo_admin, "admin_jobs_travados", nada)
    monkeypatch.setattr(repo_admin, "admin_fila_vencida_desde", vencida)
    _run(svc.vigiar_uma_vez(settings_automacao))
    assert [a["tipo"] for a in adm.agendados] == ["SINCRONIZAR_LIDERANCA"]
    assert adm.agendados[0]["payload"]["modo"] == "relatorio"


def test_sync_travada_nao_tenta_anotar_em_chamado(settings_automacao, monkeypatch):
    adm = _AdminSync(monkeypatch)
    marcados = []

    async def travados(timeout):
        return [{"id": "s1", "tipo": "SINCRONIZAR_LIDERANCA", "tentativas": 1, "worker_id": "w1",
                 "etapa_atual": dom.ETAPA_SYNC, "chamado_id": None, "aprovado_por": None, "chamado_codigo": None}]

    async def marcar(job_id, motivo, *, requeue):
        marcados.append((job_id, requeue))
        return {"id": job_id}

    async def vencida():
        return None

    monkeypatch.setattr(repo_admin, "admin_jobs_travados", travados)
    monkeypatch.setattr(repo_admin, "admin_marcar_travado", marcar)
    monkeypatch.setattr(repo_admin, "admin_fila_vencida_desde", vencida)
    _run(svc.vigiar_uma_vez(settings_automacao))
    assert marcados == [("s1", True)] and adm.mensagens == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_automacao.py -p no:cacheprovider -o addopts="" -q -k "sync or vigilancia"`
Expected: FAIL — o resultado da sync cai no fluxo de chamado (`str(job["chamado_id"])` = `"None"` e grava mensagem) / nenhuma sync agendada.

- [ ] **Step 3: Write minimal implementation** (`app/services/automacao.py`)

Logo antes de `async def processar_resultado(`:

```python
async def agendar_sincronizacao(
    settings: Settings,
    *,
    executar_apos: datetime,
    origem_job: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Enfileira uma sincronização de liderança (v2, F5). ``None`` se o tipo
    não está liberado em AUTOMACAO_TIPOS (senão a fila vencida dispararia o
    alerta de worker mudo) ou se já há uma ativa (índice da 0093)."""
    if dom.TIPO_SINCRONIZAR_LIDERANCA not in tipos_liberados(settings):
        log.info("[AUTOMACAO] sincronização de liderança não enfileirada: tipo não liberado")
        return None
    gerenciados = await repo_admin.admin_lideres_gerenciados()
    origem = (
        {"id": str(origem_job["chamado_id"]), "codigo": origem_job.get("chamado_codigo") or ""}
        if origem_job and origem_job.get("chamado_id") else None
    )
    payload = dom.montar_payload_sync(settings.automacao_sync_modo, gerenciados, origem)
    return await repo_admin.admin_agendar_job(
        chamado_id=None,
        tipo=dom.TIPO_SINCRONIZAR_LIDERANCA,
        payload=payload,
        executar_apos=executar_apos,
        aprovado_por=(str(origem_job["aprovado_por"]) if origem_job and origem_job.get("aprovado_por") else None),
    )


async def processar_resultado_sync(
    job: dict[str, Any], etapas: list[dict[str, Any]], *, settings: Settings
) -> None:
    """Resultado da sincronização: histórico de comerciais, nota no chamado de
    origem (evento) e e-mail do relatório (sempre no modo relatório)."""
    detalhes = dom.detalhes_sync(etapas)
    status_final = str(job["status"])
    comerciais = detalhes.get("lideres_comerciais")
    if isinstance(comerciais, list) and comerciais:
        try:
            await repo_admin.admin_registrar_lideres_comerciais([str(e) for e in comerciais])
        except Exception:  # noqa: BLE001
            log.exception("[AUTOMACAO] líderes comerciais não registrados (job %s)", job.get("id"))
    texto = dom.texto_relatorio_sync(job, etapas, status_final, erro_geral=job.get("erro"))
    origem = (job.get("payload") or {}).get("chamado") or {}
    if origem.get("id") and job.get("aprovado_por"):
        try:
            await repo_admin.admin_gravar_mensagem(str(origem["id"]), str(job["aprovado_por"]), texto, interna=True)
        except Exception:  # noqa: BLE001
            log.exception("[AUTOMACAO] relatório da sync não anotado no chamado %s", origem.get("codigo"))
    if dom.sync_precisa_alerta(status_final, detalhes):
        await _email_alerta_ti(
            settings,
            f"[Automação de acessos] Sincronização de liderança — {dom.STATUS_LABEL.get(status_final, status_final)}",
            texto,
        )
```

No início de `processar_resultado`, logo depois de `settings = settings or get_settings()`:

```python
    if str(job.get("tipo")) == dom.TIPO_SINCRONIZAR_LIDERANCA:
        await processar_resultado_sync(job, etapas, settings=settings)
        return
```

No fim de `processar_resultado` (depois do bloco "6) Alerta"):

```python
    # 7) Troca de supervisor/gerente mexe na liderança de muitos usuários:
    #    sincronização por evento (v2, F5), depois de a `regioes` se atualizar.
    if dom.dispara_sync_por_evento(job, etapas):
        try:
            await agendar_sincronizacao(
                settings,
                executar_apos=datetime.now(UTC) + timedelta(minutes=settings.automacao_sync_atraso_min),
                origem_job=job,
            )
        except Exception:  # noqa: BLE001
            log.exception("[AUTOMACAO] sincronização por evento não agendada (job %s)", job.get("id"))
```

Em `vigiar_uma_vez`, no laço de travados: a nota interna só quando `job.get("chamado_id")`; o link do e-mail vira
`f"{site}/workspace/chamados/{job['chamado_id']}" if job.get("chamado_id") else "(sincronização de liderança — sem chamado)"`.
Depois do laço e **antes** de `# (b) worker mudo`:

```python
    # (c) sincronização diária de liderança (v2, F5)
    try:
        agora_sync = datetime.now(UTC)
        if dom.TIPO_SINCRONIZAR_LIDERANCA in tipos_liberados(settings) and dom.sync_diaria_devida(
            agora_sync, settings.automacao_sync_hora, await repo_admin.admin_ultima_sync_criada_em()
        ):
            if await agendar_sincronizacao(settings, executar_apos=agora_sync):
                log.info("[AUTOMACAO] sincronização diária de liderança enfileirada")
    except Exception as exc:  # noqa: BLE001
        log.warning("[AUTOMACAO] sincronização diária não enfileirada: %s", exc)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_automacao.py -p no:cacheprovider -o addopts="" -q` e `ruff check app tests`
Expected: PASS.

- [ ] **Step 5: Commit** — `git add app/services/automacao.py tests/test_automacao.py`; mensagem `feat(automacao): sync de lideranca diaria e por evento, relatorio por e-mail (v2 F5)`.

### Task 9: Docs, suítes, deploy e homologação

**Files:**
- Modify (portal): `docs/automacao_api.md`, `docs/CHANGELOG.md`, `plano_md_mestre_automacao_acessos_v2.md` (Matriz F5 → 🟡; Seção 7 com `SINCRONIZAR_LIDERANCA`)
- Modify (worker): `PLANO_MESTRE_AUTOMACAO.md` (v1.4.0)

- [ ] **Step 1: Contrato** — em `docs/automacao_api.md`: tipo novo `SINCRONIZAR_LIDERANCA` (sem chamado: `chamado` = `null` no job entregue; payload `{versao, tipo, chamado: {id, codigo} | null (origem), pular_etapas: [], modo: "relatorio"|"aplicar", lideres_gerenciados: [e-mails]}`); etapa única `UBD Learning.rocks - Sincronização de Liderança` e o formato de `details`; regras (espelho com proteção, leitura por `PATCH {}` que interrompe se faltar `leader_ids`, divergência na `regioes` ⇒ fora do cálculo). Contrato continua `"1"`.
- [ ] **Step 2: Suítes** — worker: `python -m pytest tests/test_sync_lideranca.py tests/test_lideranca.py tests/test_vagas.py tests/test_worker.py tests/test_sap_usuarios.py tests/test_sap_internal_user.py tests/test_pn_fornecedor.py tests/test_wmw_cadastro.py -q`; portal: `npm run build:css && python -m pytest -p no:cacheprovider -o addopts="" -q && ruff check app tests`; e2e RLS local (`tests/e2e/test_rls_automacao_sync.py` + matriz). Tudo verde.
- [ ] **Step 3: Docs e commit** — CHANGELOG, Matriz (F5 🟡 Em processo), Seção 7, `PLANO_MESTRE_AUTOMACAO.md` v1.4.0. Commit nos dois repos; PRs (worker primeiro).
- [ ] **Step 4 (gestor): deploy, nesta ordem**
  1. Merge do PR do **worker** (o tipo novo não chega até o portal liberar).
  2. Conferir no Railway (`automacao-worker`) que `WORKER_TIPOS` está vazia **ou** contém `SINCRONIZAR_LIDERANCA`.
  3. Aplicar **0092** e depois **0093** no Supabase de produção (SQL Editor; o check `migrations-aplicadas` do CI do portal exige isso antes do merge).
  4. Merge do PR do **portal**.
  5. No serviço do portal: `AUTOMACAO_TIPOS=CRIACAO,DESLIGAMENTO,REVOGAR_LICENCA,SINCRONIZAR_LIDERANCA` (deixar `AUTOMACAO_SYNC_MODO` sem definir = `relatorio`). Se já passou das 06h, a vigilância enfileira a primeira sincronização no minuto seguinte.
- [ ] **Step 5 (gestor): homologação (DoD da Seção 8)**
  1. **Relatório:** chega o e-mail "Sincronização de liderança — MODO RELATÓRIO". Conferir 3 representantes da lista "Seriam corrigidos" contra o SAP (supervisor/gerente da região) e confirmar que nenhum líder manual aparece em "−".
  2. `AUTOMACAO_SYNC_MODO=aplicar` no portal; na manhã seguinte (06h) — ou no próximo evento — conferir os mesmos 3 representantes na UBD (*Configurações → Liderança*).
  3. **Evento:** desligar um supervisor de teste (ou devolver a vaga do teste de F3) ⇒ ~60 min depois a sync por evento remove o supervisor dos representantes da equipe e mantém o gerente; nota interna no chamado de origem.

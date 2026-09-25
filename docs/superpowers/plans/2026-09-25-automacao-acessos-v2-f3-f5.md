# Automação de Acessos v2 — F3, F4 e F5.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separar Supervisor × Gerente na criação/desligamento com ocupação/devolução da vaga na `IB_CO_REGIAO` do SAP (F3), atribuir liderança na UBD ao criar usuário — resolvida no SAP e confirmada na tabela `regioes` (F4) — e rodar o spike que decide o desenho da sincronização de 24 h (F5.0).

**Architecture:** Sem refatoração. Portal (este repo) ganha o perfil Gerente, os campos "Equipe correspondente"/"Gerência correspondente"/"Gerência responsável" e manda `vaga` e `gestor_email` no payload (contrato **v1 aditivo**). O worker (`C:\Users\Osvaldo\Downloads\Automação`, repo `OsvaldoBello/Bondmann-automacao-acessos`, branch base `master`) ganha `GERENTE`, as etapas SAP "Ocupar Vaga"/"Devolver Vaga", a resolução de líderes pelo SAP (`IB_CO_REGIAO` → e-mail do PN) com confirmação por GET na `regioes` (PostgREST, chave anon) e o `leaders` (IDs) no cadastro da UBD.

**Tech Stack:** Python 3.12, FastAPI/Jinja (portal), pytest, `requests` + fake HTTP do Service Layer (worker), SAP B1 Service Layer, MS Graph, Skore/UBD, Supabase PostgREST (somente GET).

**Spec:** [`plano_md_mestre_automacao_acessos_v2.md`](../../../plano_md_mestre_automacao_acessos_v2.md) — Seções 0 (V1–V13), 1 (F0/F0b), 4 (F3), 5 (F4), 6 (F5). Plano anterior (F0–F2, executado): [`2026-09-25-automacao-acessos-v2-f0-f2.md`](2026-09-25-automacao-acessos-v2-f0-f2.md).

**Escopo:** F3, F4 e o spike F5.0. O código da F5 (job `SINCRONIZAR_LIDERANCA`) ganha plano próprio depois do spike, porque o desenho depende do resultado (a API da UBD não devolve os líderes atuais — F0 #7).

## Global Constraints

- **`regioes` (Supabase de outro projeto) é SOMENTE LEITURA** — só `GET` no PostgREST, nunca INSERT/UPDATE/DELETE/UPSERT/DDL, nem em teste (V1).
- **Fonte dos líderes = SAP; `regioes` só confirma** (gestor, 2026-09-25). Regra de confirmação (Ruling do controlador): concorda ⇒ aplica; `regioes` indisponível ou sem linha para a região ⇒ aplica o do SAP e avisa "não confirmado na regioes"; **diverge ⇒ não aplica** a liderança daquele usuário e avisa a TI (a sincronização corrige depois).
- **Supervisor = `U_IB_CodCom3`; gerente = `U_IB_CodCom4`** (F0b). Marcadores de vaga: equipes `RH2018…RH2090` ("EQUIPE …"), gerências `RH2005 GERENTE SP`, `RH2033 GERENTE MG/RJ`. `RH2040 EQUIPE RJ 1` está inativo no SAP e **não** entra no catálogo.
- **Grupo M365 do gerente:** `Gerencia` `9bc94d0a-8dca-4f3e-86fe-44cbb7692ee9` (V10).
- **Gerentes de internos:** Alessandro `alessandro@`, Anderson `anderson@`, Elias Kirsten `elias@`, Guilherme `guilherme.rosa@`, Mariana `mariana.silva@`, Patricia `patricia.alves@`, Thiago `thiago.rodrigues@`, Rogério `rogerio@` (`@bondmann.com.br`).
- **Contrato fica em v1, só aditivo** (Ruling do controlador, desvio da Seção 7 da spec): campos novos `vaga` e perfil `GERENTE`. Ordem de deploy: **worker primeiro**, portal depois — worker antigo recebendo `GERENTE` levanta `PayloadInvalido` e o job FALHA sem tocar em sistema (fail-safe). Custo se errado: nenhum bump de versão para detectar worker desatualizado.
- **UBD `leaders` = lista de IDs numéricos** (F0 #7). Líder é aplicado **só na criação de usuário novo**; usuário que já existia na UBD não tem liderança alterada (o `PATCH` pode substituir a lista — spike F5.0).
- `step_name` são contrato — não renomear os existentes.
- Worker: nunca `git add -A`/`.`; nunca `pytest` sem arquivo (testes exploratórios de rede travam). Portal: `npm run build:css` antes da suíte completa; `ruff check app tests` limpo.
- Commits com `git commit -F <arquivo>` terminando em `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Nada de push/merge/deploy/execução real sem o "ok" do gestor.

## File Structure

| Arquivo | Repo | Responsabilidade |
|---|---|---|
| `models/domain.py`, `models/factories.py` | worker | `GERENTE`, `vaga` no payload → modelos |
| `services/ms_graph.py`, `services/learning_rocks.py` | worker | Grupo `Gerencia`; times do gerente |
| `services/sap_api.py` | worker | `find_regions_by_field`, `occupy_vacancy`, `release_vacancy`, `email_do_pn`, `lideres_por_regiao`, `gerentes_da_vaga`; `update_commission_region(gerente=)` |
| `services/regioes.py` (novo) | worker | GET somente-leitura na `regioes` (confirmação) |
| `config/settings.py` | worker | `REGIOES_API_URL`, `REGIOES_API_KEY` |
| `flow.py` | worker | Etapas Ocupar/Devolver Vaga; líderes na etapa UBD |
| `tests/test_vagas.py`, `tests/test_lideranca.py` (novos) | worker | Testes F3/F4 com fakes HTTP |
| `app/domain/vagas_comerciais.py` (novo) | portal | Catálogo de equipes/gerências |
| `app/domain/gerencias_internas.py` (novo) | portal | Gerência → e-mail do gerente |
| `app/domain/campos_dinamicos.py` | portal | `CampoDef.obrigatorio_se` |
| `app/domain/formularios_acessos.py` | portal | Perfil Gerente, campos novos, títulos |
| `app/domain/automacao.py` | portal | `vaga`, `gestor_email` resolvido, resumo |
| `tests/test_formularios_dinamicos.py`, `tests/test_automacao.py` | portal | Testes F3/F4 |
| `docs/automacao_api.md`, `docs/CHANGELOG.md`, planos | ambos | Docs |

---

## F3 — Supervisor × Gerente e vagas

### Task 1: Worker — perfil GERENTE, `vaga` no payload, grupos e times

**Files:**
- Modify: `C:\Users\Osvaldo\Downloads\Automação\models\domain.py` (`UserProfileType`, `UserCreationData`, `UserOffboardData`)
- Modify: `models/factories.py` (`_PERFIL`, `_CARGO_FIXO`, `_SETOR_FIXO`, `creation_data_from_payload`, `offboard_data_from_payload`)
- Modify: `services/ms_graph.py` (`resolve_groups_by_job_title`), `services/learning_rocks.py` (`resolve_teams_by_job_title`)
- Test: `tests/test_vagas.py` (novo)

**Interfaces:**
- Produces: `UserProfileType.GERENTE = "Gerente"`; campos `vacancy_kind: Optional[str]` (`"EQUIPE"`|`"GERENCIA"`), `vacancy_code: Optional[str]`, `vacancy_name: Optional[str]` em `UserCreationData` e `UserOffboardData`; `factories._vaga(payload) -> tuple[Optional[str], Optional[str], Optional[str]]`.

Antes de tudo: `cd "C:/Users/Osvaldo/Downloads/Automação" && git checkout master && git pull --ff-only && git checkout -b feat/v2-f3-f4`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vagas.py
"""F3 da v2 (plano do portal): perfis Supervisor × Gerente e vaga na IB_CO_REGIAO.
Rodar: python -m pytest tests/test_vagas.py -q"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import UserProfileType, creation_data_from_payload  # noqa: E402
from models.factories import PayloadInvalido, offboard_data_from_payload  # noqa: E402
from services.learning_rocks import LearningRocksService  # noqa: E402
from services.ms_graph import MSGraphService  # noqa: E402

VAGA_EQUIPE = {"tipo": "EQUIPE", "codigo": "RH2018", "nome": "EQUIPE SP 1"}
VAGA_GERENCIA = {"tipo": "GERENCIA", "codigo": "RH2005", "nome": "GERENTE SP"}
BASE = {"nome_completo": "Carla Nunes Prado", "email": "carla.prado@bondmann.com.br", "telefone": "11999998888"}


def test_criacao_supervisor_usa_vaga_e_dispensa_regiao():
    d = creation_data_from_payload({**BASE, "perfil": "SUPERVISOR", "vaga": VAGA_EQUIPE, "regiao": None})
    assert d.profile_type == UserProfileType.SUPERVISOR
    assert (d.vacancy_kind, d.vacancy_code, d.vacancy_name) == ("EQUIPE", "RH2018", "EQUIPE SP 1")
    assert d.region is None


def test_criacao_gerente_tem_cargo_e_setor_fixos():
    d = creation_data_from_payload({**BASE, "perfil": "GERENTE", "vaga": VAGA_GERENCIA})
    assert d.profile_type == UserProfileType.GERENTE
    assert d.job_title == "Gerente" and d.department == "Gerentes de vendas (sem fila)"
    assert d.vacancy_code == "RH2005"


@pytest.mark.parametrize("perfil", ["SUPERVISOR", "GERENTE"])
def test_supervisor_e_gerente_sem_vaga_sao_recusados(perfil):
    with pytest.raises(PayloadInvalido, match="vaga"):
        creation_data_from_payload({**BASE, "perfil": perfil})


def test_vaga_com_tipo_errado_para_o_perfil_e_recusada():
    with pytest.raises(PayloadInvalido, match="vaga"):
        creation_data_from_payload({**BASE, "perfil": "GERENTE", "vaga": VAGA_EQUIPE})


def test_desligamento_gerente_carrega_vaga():
    d = offboard_data_from_payload({"email": "x.y@bondmann.com.br", "nome_completo": "X Y",
                                    "perfil": "GERENTE", "vaga": VAGA_GERENCIA})
    assert d.profile_type == UserProfileType.GERENTE and d.vacancy_code == "RH2005"


def test_grupos_m365_do_gerente():
    ids, nomes = MSGraphService().resolve_groups_by_job_title(job_title="Gerente", profile_type="Gerente")
    assert "9bc94d0a-8dca-4f3e-86fe-44cbb7692ee9" in ids and "Gerencia" in nomes
    assert "f5faed6a-553c-4790-84c3-9f83b08be72d" in ids  # Grupo Bondmann
    assert "dc770076-e3c6-4554-8fd0-9e62168542e4" not in ids  # não entra em Supervisão


def test_times_ubd_do_gerente(monkeypatch):
    monkeypatch.setattr(LearningRocksService, "get_all_teams", lambda self: [])
    _, nomes = LearningRocksService().resolve_teams_by_job_title(job_title="Gerente", profile_type="Gerente")
    assert "Bondmann" in nomes and "Lideranças" in nomes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vagas.py -q`
Expected: FAIL — `AttributeError: GERENTE` / `vacancy_kind` inexistente.

- [ ] **Step 3: Write minimal implementation**

`models/domain.py` — em `UserProfileType`, depois de `SUPERVISOR = "Supervisor"`:

```python
    GERENTE = "Gerente"
```

Em `UserCreationData`, logo depois de `region_name`, e em `UserOffboardData`, logo depois de `region_name`:

```python
    # Vaga comercial (plano v2, F3): marcador RH20xx da equipe (supervisor) ou
    # da gerência (gerente) na IB_CO_REGIAO do SAP.
    vacancy_kind: Optional[str] = None  # "EQUIPE" | "GERENCIA"
    vacancy_code: Optional[str] = None  # ex.: "RH2018"
    vacancy_name: Optional[str] = None  # ex.: "EQUIPE SP 1"
```

`models/factories.py`:

```python
_PERFIL = {
    "REPRESENTANTE": UserProfileType.REPRESENTANTE,
    "SUPERVISOR": UserProfileType.SUPERVISOR,
    "GERENTE": UserProfileType.GERENTE,
    "INTERNO": UserProfileType.INTERNO,
    "SDR": UserProfileType.SDR,
}

_CARGO_FIXO = {
    UserProfileType.REPRESENTANTE: "Representante",
    UserProfileType.SUPERVISOR: "Supervisor",
    UserProfileType.GERENTE: "Gerente",
}
_SETOR_FIXO = {
    UserProfileType.REPRESENTANTE: "Representantes (sem fila)",
    UserProfileType.SUPERVISOR: "Supervisão de Vendas (sem fila)",
    UserProfileType.GERENTE: "Gerentes de vendas (sem fila)",
}
# Tipo de vaga exigido por perfil (plano v2, F3).
_VAGA_DO_PERFIL = {UserProfileType.SUPERVISOR: "EQUIPE", UserProfileType.GERENTE: "GERENCIA"}


def _vaga(payload: Dict[str, Any], perfil: UserProfileType) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """`vaga` do contrato → (kind, code, name). Obrigatória e do tipo certo
    para SUPERVISOR/GERENTE; ignorada nos demais perfis."""
    esperado = _VAGA_DO_PERFIL.get(perfil)
    if esperado is None:
        return None, None, None
    bruto = payload.get("vaga")
    if not isinstance(bruto, dict) or not _texto(bruto.get("codigo")):
        raise PayloadInvalido(f"vaga obrigatória para perfil {perfil.name}")
    tipo = _texto(bruto.get("tipo")).upper()
    if tipo != esperado:
        raise PayloadInvalido(f"vaga do tipo {tipo or '(vazio)'} não serve para perfil {perfil.name} (esperado {esperado})")
    return tipo, _texto(bruto.get("codigo")).upper(), _texto(bruto.get("nome")) or None
```

Em `creation_data_from_payload`: trocar

```python
    if perfil in (UserProfileType.REPRESENTANTE, UserProfileType.SUPERVISOR) and not region:
        raise PayloadInvalido(f"regiao obrigatória para perfil {perfil.name}")
```

por

```python
    if perfil == UserProfileType.REPRESENTANTE and not region:
        raise PayloadInvalido(f"regiao obrigatória para perfil {perfil.name}")
    vaga_kind, vaga_code, vaga_name = _vaga(payload, perfil)
```

e acrescentar `vacancy_kind=vaga_kind, vacancy_code=vaga_code, vacancy_name=vaga_name,` no `UserCreationData(...)`. Em `offboard_data_from_payload`, depois de `perfil = _perfil(payload)`: `vaga_kind, vaga_code, vaga_name = _vaga(payload, perfil)` e os mesmos três argumentos no `UserOffboardData(...)`.

`services/ms_graph.py`, em `resolve_groups_by_job_title`, antes de `elif "SDR" in prof_str:`:

```python
        elif "GERENTE" in prof_str:
            # Grupo Bondmann já adicionado no passo 1; grupo de gerência (V10).
            add_item("9bc94d0a-8dca-4f3e-86fe-44cbb7692ee9", "Gerencia")  # bd.gerencia@bondmann.com.br
```

`services/learning_rocks.py`, em `resolve_teams_by_job_title`, antes de `elif "SDR" in prof_str:`:

```python
        elif "GERENTE" in prof_str:
            t_id = team_map.get("lideranças", default_team_ids.get("lideranças"))
            if t_id and t_id not in assigned_ids:
                assigned_ids.append(t_id)
                assigned_names.append("Lideranças")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vagas.py tests/test_worker.py tests/test_sap_usuarios.py -q`
Expected: PASS (todos). Se algum teste de `test_worker.py` montar payload `SUPERVISOR` só com `regiao`, acrescente `"vaga": {"tipo": "EQUIPE", "codigo": "RH2018", "nome": "EQUIPE SP 1"}` nele (mudança pretendida do contrato).

- [ ] **Step 5: Commit**

```bash
git add models/domain.py models/factories.py services/ms_graph.py services/learning_rocks.py tests/test_vagas.py tests/test_worker.py
git commit -F msg.txt   # msg.txt: "feat(worker): perfil GERENTE e vaga comercial no payload (v2 F3)", linha em branco, trailer Co-Authored-By
```

### Task 2: Worker — ocupar e devolver vaga na `IB_CO_REGIAO`

**Files:**
- Modify: `services/sap_api.py` (`update_commission_region` + métodos novos depois de `deactivate_sales_person`)
- Test: `tests/test_vagas.py`

**Interfaces:**
- Consumes: `find_business_partner(email, name)`, `self.session`, `self.base_url`, `self.login()`.
- Produces:
  - `CAMPO_VAGA = {"EQUIPE": "U_IB_CodCom3", "GERENCIA": "U_IB_CodCom4"}` (módulo)
  - `SAPService.find_regions_by_field(campo: str, valor: str) -> List[Dict[str, Any]]`
  - `SAPService.occupy_vacancy(kind: str, vacancy_code: str, email: str, full_name: str) -> Dict[str, Any]` → `{"card_code", "campo", "regioes": [Code...]}`; `RuntimeError` se PN não achado ou se nenhuma região tem o marcador
  - `SAPService.release_vacancy(kind: str, vacancy_code: str, email: str, full_name: str) -> Dict[str, Any]` → `{"card_code", "campo", "regioes", "note"?}`
  - `update_commission_region(..., gerente: Optional[str] = None)` grava `U_IB_CodCom4`

- [ ] **Step 1: Write the failing test** (acrescentar em `tests/test_vagas.py`)

```python
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from config import settings  # noqa: E402
from services.sap_api import SAPService  # noqa: E402


class FakeSAPRegioes:
    """Service Layer mínima para IB_CO_REGIAO e BusinessPartners."""

    def __init__(self, regioes, pns):
        self.regioes = {r["Code"]: dict(r) for r in regioes}
        self.pns = list(pns)
        self.patches: list[tuple[str, dict]] = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None):
                raw = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def do_POST(self):
                self._body()
                self._send(200, {"SessionId": "s"}) if self.path.endswith("/Login") else self._send(204)

            def do_GET(self):
                partes = urlsplit(self.path)
                filtro = unquote(parse_qs(partes.query).get("$filter", [""])[0])
                if partes.path.endswith("/IB_CO_REGIAO"):
                    m = re.match(r"^(U_IB_CodCom\d) eq '(.*)'$", filtro)
                    itens = [r for r in fake.regioes.values() if not m or r.get(m.group(1)) == m.group(2)]
                    return self._send(200, {"value": itens})
                if partes.path.endswith("/BusinessPartners"):
                    m = re.search(r"'([^']*)'", filtro)
                    alvo = (m.group(1) if m else "").lower()
                    itens = [p for p in fake.pns if alvo and (alvo == (p.get("EmailAddress") or "").lower()
                             or alvo == p["CardCode"].lower() or alvo in p["CardName"].lower())]
                    return self._send(200, {"value": itens})
                self._send(404, {})

            def do_PATCH(self):
                corpo = self._body()
                m = re.search(r"IB_CO_REGIAO\('([^']*)'\)", unquote(self.path))
                code = m.group(1)
                fake.patches.append((code, corpo))
                fake.regioes[code].update(corpo)
                self._send(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/b1s/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


PN_CARLA = {"CardCode": "RH2099", "CardName": "CARLA NUNES PRADO", "CardType": "cSupplier",
            "EmailAddress": "carla.prado@bondmann.com.br"}


@pytest.fixture
def sap_regioes(monkeypatch):
    monkeypatch.setattr(settings, "SAP_PROXY_URL", "", raising=False)
    fakes = []

    def _fabrica(regioes, pns=(PN_CARLA,)):
        fake = FakeSAPRegioes(regioes, pns)
        fakes.append(fake)
        svc = SAPService(base_url=fake.url, company_db="T", username="u", password="p")
        svc.session.trust_env = False
        return fake, svc

    yield _fabrica
    for f in fakes:
        f.close()


def test_ocupa_vaga_de_equipe_em_todas_as_regioes_do_marcador(sap_regioes):
    fake, svc = sap_regioes([
        {"Code": "001-A", "U_IB_CodCom3": "RH2018", "U_IB_CodCom4": "RH2004"},
        {"Code": "002-B", "U_IB_CodCom3": "RH2018", "U_IB_CodCom4": "RH2004"},
        {"Code": "003-C", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"},
    ])
    res = svc.occupy_vacancy("EQUIPE", "RH2018", "carla.prado@bondmann.com.br", "Carla Nunes Prado")
    assert res["card_code"] == "RH2099" and res["campo"] == "U_IB_CodCom3"
    assert sorted(res["regioes"]) == ["001-A", "002-B"]
    assert sorted(fake.patches) == [("001-A", {"U_IB_CodCom3": "RH2099"}), ("002-B", {"U_IB_CodCom3": "RH2099"})]


def test_ocupa_vaga_de_gerencia_usa_codcom4(sap_regioes):
    fake, svc = sap_regioes([{"Code": "001-A", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2005"}])
    res = svc.occupy_vacancy("GERENCIA", "RH2005", "carla.prado@bondmann.com.br", "Carla Nunes Prado")
    assert res["campo"] == "U_IB_CodCom4" and fake.patches == [("001-A", {"U_IB_CodCom4": "RH2099"})]


def test_vaga_sem_regioes_no_marcador_falha_com_quem_ocupa(sap_regioes):
    _, svc = sap_regioes([{"Code": "001-A", "U_IB_CodCom3": "RH2052", "U_IB_CodCom4": "RH2004"}])
    with pytest.raises(RuntimeError, match="RH2018"):
        svc.occupy_vacancy("EQUIPE", "RH2018", "carla.prado@bondmann.com.br", "Carla Nunes Prado")


def test_pn_nao_encontrado_falha_sem_gravar(sap_regioes):
    fake, svc = sap_regioes([{"Code": "001-A", "U_IB_CodCom3": "RH2018"}], pns=())
    with pytest.raises(RuntimeError, match="cadastre o PN"):
        svc.occupy_vacancy("EQUIPE", "RH2018", "carla.prado@bondmann.com.br", "Carla Nunes Prado")
    assert fake.patches == []


def test_reexecucao_idempotente_regiao_ja_com_o_pn_conta_como_feita(sap_regioes):
    fake, svc = sap_regioes([
        {"Code": "001-A", "U_IB_CodCom3": "RH2099"},
        {"Code": "002-B", "U_IB_CodCom3": "RH2018"},
    ])
    res = svc.occupy_vacancy("EQUIPE", "RH2018", "carla.prado@bondmann.com.br", "Carla Nunes Prado")
    assert sorted(res["regioes"]) == ["001-A", "002-B"] and fake.patches == [("002-B", {"U_IB_CodCom3": "RH2099"})]


def test_devolve_vaga_volta_o_marcador(sap_regioes):
    fake, svc = sap_regioes([
        {"Code": "001-A", "U_IB_CodCom4": "RH2099"},
        {"Code": "002-B", "U_IB_CodCom4": "RH2004"},
    ])
    res = svc.release_vacancy("GERENCIA", "RH2005", "carla.prado@bondmann.com.br", "Carla Nunes Prado")
    assert res["regioes"] == ["001-A"] and fake.patches == [("001-A", {"U_IB_CodCom4": "RH2005"})]


def test_devolver_sem_regioes_e_nada_a_devolver(sap_regioes):
    fake, svc = sap_regioes([{"Code": "001-A", "U_IB_CodCom3": "RH2052"}])
    res = svc.release_vacancy("EQUIPE", "RH2018", "carla.prado@bondmann.com.br", "Carla Nunes Prado")
    assert res["regioes"] == [] and "nada a devolver" in res["note"] and fake.patches == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vagas.py -q`
Expected: FAIL — `AttributeError: 'SAPService' object has no attribute 'occupy_vacancy'`.

- [ ] **Step 3: Write minimal implementation**

`services/sap_api.py`, no topo junto das constantes:

```python
# Campo da IB_CO_REGIAO por tipo de vaga (plano v2, F0b): supervisor = CodCom3,
# gerente = CodCom4. Com a vaga aberta o campo guarda o marcador RH20xx da
# equipe/gerência; ocupada, o CardCode do PN da pessoa.
CAMPO_VAGA = {"EQUIPE": "U_IB_CodCom3", "GERENCIA": "U_IB_CodCom4"}
```

Em `update_commission_region`: assinatura ganha `gerente: Optional[str] = None,` depois de `supervisor`, docstring cita `U_IB_CodCom4 = Gerente`, e depois de `if supervisor is not None: payload["U_IB_CodCom3"] = supervisor`:

```python
        if gerente is not None:
            payload["U_IB_CodCom4"] = gerente
```

Métodos novos, logo depois de `deactivate_sales_person`:

```python
    def find_regions_by_field(self, campo: str, valor: str) -> List[Dict[str, Any]]:
        """Regiões cuja coluna ``campo`` (U_IB_CodCom3/4) vale ``valor`` (paginado)."""
        self.login()
        regioes: List[Dict[str, Any]] = []
        url: Optional[str] = f"{self.base_url}/IB_CO_REGIAO"
        params: Optional[Dict[str, str]] = {"$filter": f"{campo} eq '{_odata(valor)}'"}
        while url:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                raise RuntimeError(f"SAP respondeu {resp.status_code} ao buscar regiões [{campo} = {valor}]: {resp.text[:300]}")
            corpo = resp.json()
            regioes += corpo.get("value", [])
            prox = corpo.get("odata.nextLink")
            url = (prox if prox.startswith("http") else f"{self.base_url}/{prox.lstrip('/')}") if prox else None
            params = None
        return regioes

    def _pn_da_pessoa(self, email: str, full_name: str) -> Dict[str, Any]:
        pn = self.find_business_partner(email=email, name=full_name)
        if not pn or not pn.get("CardCode"):
            raise RuntimeError(
                f"PN de {full_name} ({email}) não encontrado no SAP — cadastre o PN e reexecute só esta etapa"
            )
        return pn

    def occupy_vacancy(self, kind: str, vacancy_code: str, email: str, full_name: str) -> Dict[str, Any]:
        """Troca o marcador da vaga pelo PN da pessoa em todas as regiões da
        equipe/gerência (V3). Idempotente: regiões que já têm o PN contam como feitas."""
        campo = CAMPO_VAGA[kind]
        card_code = self._pn_da_pessoa(email, full_name)["CardCode"]
        ja_ocupadas = [r["Code"] for r in self.find_regions_by_field(campo, card_code)]
        pendentes = [r["Code"] for r in self.find_regions_by_field(campo, vacancy_code)]
        if not pendentes and not ja_ocupadas:
            raise RuntimeError(
                f"Nenhuma região com {campo} = {vacancy_code} (vaga aberta) — a vaga parece ocupada; "
                "confira se o antecessor foi desligado"
            )
        feitas = list(ja_ocupadas)
        for code in pendentes:
            if not self._patch_regiao(code, {campo: card_code}):
                raise RuntimeError(f"SAP recusou gravar {campo} = {card_code} na região {code}; já gravadas: {feitas}")
            feitas.append(code)
        return {"card_code": card_code, "campo": campo, "regioes": feitas}

    def release_vacancy(self, kind: str, vacancy_code: str, email: str, full_name: str) -> Dict[str, Any]:
        """Volta o marcador da vaga nas regiões que apontam para o PN da pessoa."""
        campo = CAMPO_VAGA[kind]
        pn = self.find_business_partner(email=email, name=full_name)
        if not pn or not pn.get("CardCode"):
            return {"card_code": None, "campo": campo, "regioes": [], "note": "PN não encontrado — nada a devolver"}
        card_code = pn["CardCode"]
        regioes = [r["Code"] for r in self.find_regions_by_field(campo, card_code)]
        for code in regioes:
            if not self._patch_regiao(code, {campo: vacancy_code}):
                raise RuntimeError(f"SAP recusou devolver {campo} = {vacancy_code} na região {code}")
        res: Dict[str, Any] = {"card_code": card_code, "campo": campo, "regioes": regioes}
        if not regioes:
            res["note"] = f"nenhuma região com {campo} = {card_code} — nada a devolver"
        return res

    def _patch_regiao(self, code: str, corpo: Dict[str, Any]) -> bool:
        import urllib.parse

        resp = self.session.patch(f"{self.base_url}/IB_CO_REGIAO('{urllib.parse.quote(code)}')", json=corpo, timeout=10)
        return resp.status_code in (200, 204)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vagas.py tests/test_sap_usuarios.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `git add services/sap_api.py tests/test_vagas.py`; mensagem `feat(sap): ocupar e devolver vaga de equipe/gerencia na IB_CO_REGIAO (v2 F3)`.

### Task 3: Worker — etapas "Ocupar Vaga" e "Devolver Vaga" no `flow.py`

**Files:**
- Modify: `flow.py` (constantes de etapa; `run_user_creation_flow` ETAPA 5; `run_user_offboard_flow` ETAPA 3/4)
- Test: `tests/test_vagas.py`

**Interfaces:**
- Consumes: `occupy_vacancy`, `release_vacancy` (Task 2); `data.vacancy_*` (Task 1).
- Produces: `STEP_SAP_OCUPAR_VAGA = "SAP Business One (Service Layer) - Ocupar Vaga (Equipe/Gerência)"`, `STEP_SAP_DEVOLVER_VAGA = "SAP Business One (Service Layer) - Devolver Vaga (Equipe/Gerência)"`.

- [ ] **Step 1: Write the failing test**

```python
from flow import (  # noqa: E402
    STEP_M365_CRIACAO, STEP_M365_DESLIG, STEP_SAP_DESLIG_RH2020, STEP_SAP_DEVOLVER_VAGA,
    STEP_SAP_OCUPAR_VAGA, STEP_UBD_CRIACAO, STEP_UBD_DESLIG, AutomationFlowOrchestrator,
)


class SapVagaStub:
    def __init__(self):
        self.chamadas = []

    def occupy_vacancy(self, kind, code, email, name):
        self.chamadas.append(("ocupar", kind, code, email))
        return {"card_code": "RH2099", "campo": "U_IB_CodCom3", "regioes": ["001-A"]}

    def release_vacancy(self, kind, code, email, name):
        self.chamadas.append(("devolver", kind, code, email))
        return {"card_code": "RH2099", "campo": "U_IB_CodCom3", "regioes": ["001-A"]}

    def login(self):
        return "s"

    def logout(self):
        return True


def _orq(monkeypatch, pular):
    monkeypatch.setattr(settings, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(settings, "validate_sap", lambda: True)
    orq = AutomationFlowOrchestrator(dry_run=False, interactive=False, skip_steps=pular)
    orq.sap = SapVagaStub()
    return orq


def test_criacao_supervisor_ocupa_a_vaga(monkeypatch):
    orq = _orq(monkeypatch, [STEP_M365_CRIACAO, STEP_UBD_CRIACAO])
    res = orq.run_user_creation_flow(creation_data_from_payload({**BASE, "perfil": "SUPERVISOR", "vaga": VAGA_EQUIPE}))
    etapa = next(r for r in res if r.step_name == STEP_SAP_OCUPAR_VAGA)
    assert etapa.status.value == "SUCCESS"
    assert orq.sap.chamadas == [("ocupar", "EQUIPE", "RH2018", "carla.prado@bondmann.com.br")]


def test_desligamento_gerente_devolve_a_vaga_e_nao_passa_pelo_rh2020(monkeypatch):
    orq = _orq(monkeypatch, [STEP_M365_DESLIG, STEP_UBD_DESLIG])
    res = orq.run_user_offboard_flow(offboard_data_from_payload({"email": "carla.prado@bondmann.com.br",
        "nome_completo": "Carla Nunes Prado", "perfil": "GERENTE", "vaga": VAGA_GERENCIA}))
    nomes = [r.step_name for r in res]
    assert STEP_SAP_DEVOLVER_VAGA in nomes and STEP_SAP_DESLIG_RH2020 not in nomes
    assert orq.sap.chamadas == [("devolver", "GERENCIA", "RH2005", "carla.prado@bondmann.com.br")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_vagas.py -q -k "ocupa_a_vaga or devolve_a_vaga"`
Expected: FAIL — `ImportError: cannot import name 'STEP_SAP_OCUPAR_VAGA'`.

- [ ] **Step 3: Write minimal implementation**

`flow.py`, junto das constantes:

```python
STEP_SAP_OCUPAR_VAGA = "SAP Business One (Service Layer) - Ocupar Vaga (Equipe/Gerência)"
STEP_SAP_DEVOLVER_VAGA = "SAP Business One (Service Layer) - Devolver Vaga (Equipe/Gerência)"
_PERFIS_COM_VAGA = (UserProfileType.SUPERVISOR, UserProfileType.GERENTE)
```

Criação — no fim da ETAPA 5, depois do bloco `elif data.profile_type == UserProfileType.REPRESENTANTE ...:` e antes do `return results` final:

```python
        elif data.profile_type in _PERFIS_COM_VAGA and data.vacancy_code:
            def _ocupar_vaga() -> Dict[str, Any]:
                if self.dry_run:
                    return {"status": "Simulado (Dry Run)", "vaga": data.vacancy_code, "tipo": data.vacancy_kind}
                if not settings.validate_sap():
                    raise RuntimeError("Credenciais SAP não configuradas")
                try:
                    return self.sap.occupy_vacancy(data.vacancy_kind, data.vacancy_code, target_email, data.full_name)
                finally:
                    self.sap.logout()

            if not self._run_step(results, STEP_SAP_OCUPAR_VAGA, _ocupar_vaga):
                return results
```

Desligamento — a ETAPA 4 passa a ter três ramos. Trocar `else:` (antes de `def _sap_rh2020`) por:

```python
        elif data.profile_type in _PERFIS_COM_VAGA and data.vacancy_code:
            def _devolver_vaga() -> Dict[str, Any]:
                if self.dry_run:
                    return {"status": "Simulado (Dry Run)", "vaga": data.vacancy_code, "tipo": data.vacancy_kind}
                if not settings.validate_sap():
                    raise RuntimeError("Credenciais SAP não configuradas")
                try:
                    return self.sap.release_vacancy(data.vacancy_kind, data.vacancy_code, data.email, data.full_name or data.email)
                finally:
                    self.sap.logout()

            if not self._run_step(results, STEP_SAP_DEVOLVER_VAGA, _devolver_vaga):
                return results
        else:
```

(o ramo `_sap_rh2020` continua para REPRESENTANTE e para SUPERVISOR legado sem `vaga`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_vagas.py tests/test_worker.py tests/test_sap_usuarios.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `git add flow.py tests/test_vagas.py`; mensagem `feat(flow): etapas Ocupar/Devolver Vaga para supervisor e gerente (v2 F3)`.

### Task 4: Portal — catálogo de vagas, perfil Gerente e campos Equipe/Gerência

**Files:**
- Create: `app/domain/vagas_comerciais.py`
- Modify: `app/domain/formularios_acessos.py` (perfis, `_SO_*`, `campos_criacao`, `CAMPOS_DESLIGAMENTO`, `perfil_curto`, `titulo_e_descricao_automaticos`)
- Modify: `app/domain/automacao.py` (`_PERFIL_ENUM`, `montar_payload`, `resumo_payload`)
- Test: `tests/test_formularios_dinamicos.py`, `tests/test_automacao.py`

**Interfaces:**
- Produces: `vagas_comerciais.EQUIPES: tuple[tuple[str, str], ...]`, `GERENCIAS`, `rotulo(codigo, nome) -> str` (`"RH2018 — EQUIPE SP 1"`), `vaga_do_rotulo(rotulo) -> dict | None` (`{"tipo","codigo","nome"}`); `ac.PERFIL_GERENTE = "Gerente de Vendas"`; payload `vaga`.

Branch: `git checkout claude/develop && git pull --ff-only && git checkout -b feat/automacao-v2-f3-f4`.

- [ ] **Step 1: Write the failing test**

Em `tests/test_formularios_dinamicos.py`:

```python
from app.domain import vagas_comerciais as vc


def test_catalogo_de_vagas_do_sap():
    codigos = [c for c, _ in vc.EQUIPES]
    assert "RH2018" in codigos and "RH2090" in codigos and "RH2040" not in codigos  # RH2040 inativo
    assert len(codigos) == len(set(codigos)) == 21
    assert vc.GERENCIAS == (("RH2005", "GERENTE SP"), ("RH2033", "GERENTE MG/RJ"))
    assert vc.vaga_do_rotulo("RH2018 — EQUIPE SP 1") == {"tipo": "EQUIPE", "codigo": "RH2018", "nome": "EQUIPE SP 1"}
    assert vc.vaga_do_rotulo("RH2005 — GERENTE SP")["tipo"] == "GERENCIA"
    assert vc.vaga_do_rotulo("qualquer coisa") is None


def test_criacao_supervisor_pede_equipe_e_nao_regiao():
    dados = _criacao_representante(perfil=[ac.PERFIL_SUPERVISOR], regiao_wmw=[""], dispositivo_wmw=[""],
                                   equipe=["RH2018 — EQUIPE SP 1"])
    ok, erro, limpo = validar_campos(ac.campos_criacao(SETORES), dados)
    assert ok, erro
    assert limpo["equipe"] == "RH2018 — EQUIPE SP 1" and "regiao_wmw" not in limpo
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), {**dados, "equipe": [""]})
    assert not ok and "Equipe correspondente" in erro


def test_criacao_gerente_pede_gerencia():
    dados = _criacao_representante(perfil=[ac.PERFIL_GERENTE], regiao_wmw=[""], dispositivo_wmw=[""],
                                   gerencia=["RH2033 — GERENTE MG/RJ"])
    ok, erro, limpo = validar_campos(ac.campos_criacao(SETORES), dados)
    assert ok, erro
    assert limpo["gerencia"] == "RH2033 — GERENTE MG/RJ" and "equipe" not in limpo


def test_desligamento_supervisor_pede_equipe():
    ok, erro, limpo = validar_campos(
        ac.CAMPOS_DESLIGAMENTO,
        _desligamento(perfil=[ac.PERFIL_SUPERVISOR], regiao=[""], equipe=["RH2021 — EQUIPE MG 2"]),
    )
    assert ok, erro
    assert limpo["equipe"] == "RH2021 — EQUIPE MG 2" and "regiao" not in limpo
```

Substituir o teste antigo `test_criacao_supervisor_pede_regiao_mas_nao_dispositivo` (Supervisor não usa mais região) pelo `test_criacao_supervisor_pede_equipe_e_nao_regiao` acima.

Em `tests/test_automacao.py`, trocar `DADOS_DESLIG` para supervisor com equipe:

```python
DADOS_DESLIG = {
    "email": "joao.souza@bondmann.com.br",
    "nome_completo": "João Pedro Souza",
    "perfil": ac.PERFIL_SUPERVISOR,
    "data_desligamento": "2026-09-30",
    "equipe": "RH2021 — EQUIPE MG 2",
    "motivo": "Encerramento de Contrato",
    "encaminhar_para": "pedidos@bondmann.com.br",
}
```

e `test_payload_desligamento` passa a afirmar:

```python
    assert p["perfil"] == "SUPERVISOR" and p["regiao"] is None
    assert p["vaga"] == {"tipo": "EQUIPE", "codigo": "RH2021", "nome": "EQUIPE MG 2"}
```

mais um teste novo:

```python
def test_payload_criacao_gerente_leva_vaga_de_gerencia():
    dados = {**DADOS_CRIACAO, "perfil": ac.PERFIL_GERENTE, "regiao_wmw": "", "gerencia": "RH2005 — GERENTE SP"}
    p = dom.montar_payload(dom.TIPO_CRIACAO, dados, CHAMADO)
    assert p["perfil"] == "GERENTE" and p["regiao"] is None
    assert p["vaga"] == {"tipo": "GERENCIA", "codigo": "RH2005", "nome": "GERENTE SP"}
    assert ("Vaga", "RH2005 — GERENTE SP") in dom.resumo_payload(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formularios_dinamicos.py tests/test_automacao.py -p no:cacheprovider -k "vaga or supervisor or gerente or payload_desligamento"`
Expected: FAIL — `ModuleNotFoundError: app.domain.vagas_comerciais`.

- [ ] **Step 3: Write minimal implementation**

`app/domain/vagas_comerciais.py`:

```python
"""Vagas comerciais do SAP (plano v2, F3): marcadores de equipe (supervisor,
`U_IB_CodCom3`) e de gerência (gerente, `U_IB_CodCom4`) na IB_CO_REGIAO.

Extraído do SAP em 2026-09-25 (BusinessPartners `RH*` com nome EQUIPE/GERENTE,
só os ativos — `RH2040 EQUIPE RJ 1` está inativo). Equipe nova = uma linha aqui.
"""

from __future__ import annotations

SEPARADOR = " — "

EQUIPES: tuple[tuple[str, str], ...] = (
    ("RH2018", "EQUIPE SP 1"), ("RH2019", "EQUIPE SP 2"), ("RH2021", "EQUIPE MG 2"),
    ("RH2024", "EQUIPE SP 5"), ("RH2029", "EQUIPE RS 1"), ("RH2032", "EQUIPE PR 2"),
    ("RH2039", "EQUIPE SP 6"), ("RH2041", "EQUIPE PR 1"), ("RH2043", "EQUIPE MG 1"),
    ("RH2047", "EQUIPE SP 3"), ("RH2048", "EQUIPE MG 3"), ("RH2053", "EQUIPE SC 1"),
    ("RH2054", "EQUIPE PR 4"), ("RH2055", "EQUIPE GO1"), ("RH2074", "EQUIPE PR3"),
    ("RH2075", "EQUIPE RS 2"), ("RH2082", "EQUIPE SC 2"), ("RH2083", "EQUIPE RS 5"),
    ("RH2084", "EQUIPE PR 5"), ("RH2086", "EQUIPE SP 4"), ("RH2090", "EQUIPE DIRETA SP"),
)
GERENCIAS: tuple[tuple[str, str], ...] = (("RH2005", "GERENTE SP"), ("RH2033", "GERENTE MG/RJ"))


def rotulo(codigo: str, nome: str) -> str:
    return f"{codigo}{SEPARADOR}{nome}"


ROTULOS_EQUIPES = tuple(rotulo(c, n) for c, n in EQUIPES)
ROTULOS_GERENCIAS = tuple(rotulo(c, n) for c, n in GERENCIAS)


def vaga_do_rotulo(valor: str | None) -> dict[str, str] | None:
    """``"RH2018 — EQUIPE SP 1"`` → ``{"tipo": "EQUIPE", "codigo": ..., "nome": ...}``."""
    texto = (valor or "").strip()
    for tipo, catalogo in (("EQUIPE", EQUIPES), ("GERENCIA", GERENCIAS)):
        for codigo, nome in catalogo:
            if texto == rotulo(codigo, nome):
                return {"tipo": tipo, "codigo": codigo, "nome": nome}
    return None
```

`app/domain/formularios_acessos.py`:
- import: `from app.domain import vagas_comerciais as vc`
- perfis:

```python
PERFIL_SUPERVISOR = "Supervisor / Liderança de Equipe"
PERFIL_GERENTE = "Gerente de Vendas"
_PERFIS = (PERFIL_REPRESENTANTE, PERFIL_INTERNO, PERFIL_SUPERVISOR, PERFIL_GERENTE)
```

- condicionais: trocar `_SO_COMERCIAIS = ("perfil", (PERFIL_REPRESENTANTE, PERFIL_SUPERVISOR))` por

```python
_SO_SUPERVISOR = ("perfil", (PERFIL_SUPERVISOR,))
_SO_GERENTE = ("perfil", (PERFIL_GERENTE,))
```

- em `campos_criacao`, o campo `regiao_wmw` passa a `visivel_se=_SO_REPRESENTANTE`, e logo depois dele:

```python
        CampoDef(
            "equipe", "Equipe correspondente", "select", obrigatorio=True,
            opcoes=vc.ROTULOS_EQUIPES, visivel_se=_SO_SUPERVISOR,
            ajuda="Equipe do SAP que o supervisor assume (o SAP troca o marcador da equipe "
            "pelo cadastro dele em todas as regiões).",
        ),
        CampoDef(
            "gerencia", "Gerência correspondente", "select", obrigatorio=True,
            opcoes=vc.ROTULOS_GERENCIAS, visivel_se=_SO_GERENTE,
        ),
```

- em `CAMPOS_DESLIGAMENTO`, o campo `regiao` passa a `visivel_se=_SO_REPRESENTANTE` com ajuda "A região é transferida para RH2020 no SAP.", e logo depois dele os mesmos `equipe` (ajuda: "O marcador da equipe volta para as regiões do supervisor desligado.") e `gerencia`.
- `perfil_curto`: `if valor == PERFIL_GERENTE: return "Gerente"`.
- `titulo_e_descricao_automaticos`: nos dois ramos, depois da linha "Perfil: …", acrescentar

```python
        vaga = dados.get("equipe") or dados.get("gerencia")
        if vaga:
            partes.append(f"Vaga: {vaga}")
```

`app/domain/automacao.py`:
- `from app.domain import vagas_comerciais as vc`
- `_PERFIL_ENUM` ganha `ac.PERFIL_GERENTE: "GERENTE",`
- em `montar_payload`, logo depois de `perfil = ...`: `vaga = vc.vaga_do_rotulo(dados.get("equipe") or dados.get("gerencia"))`; nos dois ramos, acrescentar `"vaga": vaga,`.
- em `resumo_payload`, depois de `_add("Região", ...)`:

```python
    vaga = payload.get("vaga")
    _add("Vaga", vc.rotulo(vaga["codigo"], vaga["nome"]) if isinstance(vaga, dict) else None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_formularios_dinamicos.py tests/test_automacao.py tests/test_portal.py -p no:cacheprovider` e `ruff check app tests`
Expected: PASS, ruff limpo.

- [ ] **Step 5: Commit** — `git add app/domain/vagas_comerciais.py app/domain/formularios_acessos.py app/domain/automacao.py tests/test_formularios_dinamicos.py tests/test_automacao.py`; mensagem `feat(automacao): perfil Gerente e campos Equipe/Gerencia correspondente (v2 F3)`.

---

## F4 — Liderança na UBD ao criar usuário

### Task 5: Portal — "Gerência responsável" do interno e `gestor_email` resolvido

**Files:**
- Create: `app/domain/gerencias_internas.py`
- Modify: `app/domain/campos_dinamicos.py` (`CampoDef.obrigatorio_se`, `validar_campos`)
- Modify: `app/domain/formularios_acessos.py` (`campos_criacao`), `app/domain/automacao.py` (`montar_payload` CRIACAO)
- Test: `tests/test_formularios_dinamicos.py`, `tests/test_automacao.py`

**Interfaces:**
- Produces: `CampoDef.obrigatorio_se: tuple[str, tuple[str, ...]] | None`; `gerencias_internas.OUTRA = "Outra (informar o e-mail do gestor)"`, `GERENCIAS: tuple[tuple[str, str], ...]` (rótulo, e-mail), `OPCOES`, `email_do_gerente(rotulo) -> str | None`; payload CRIACAO `gestor_email` = e-mail do gerente escolhido (ou o digitado, com "Outra").

- [ ] **Step 1: Write the failing test**

```python
from app.domain import gerencias_internas as gi


def test_obrigatorio_se_exige_o_campo_so_na_condicao():
    campos = (
        CampoDef("tipo", "Tipo", "select", obrigatorio=True, opcoes=("A", "B")),
        CampoDef("detalhe", "Detalhe", "text", obrigatorio_se=("tipo", ("B",))),
    )
    assert validar_campos(campos, {"tipo": ["A"]})[0]
    ok, erro, _ = validar_campos(campos, {"tipo": ["B"]})
    assert not ok and "Detalhe" in erro


def test_gerencias_internas_mapeiam_para_o_email_do_gerente():
    assert gi.email_do_gerente("PCP / Recebimento / Expedição — Elias Kirsten") == "elias@bondmann.com.br"
    assert gi.email_do_gerente(gi.OUTRA) is None and len(gi.GERENCIAS) == 8


def test_criacao_interno_exige_gerencia_e_email_so_com_outra():
    base = {k: v for k, v in _criacao_interno().items() if k != "gerencia_interna"}
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), base)
    assert not ok and "Gerência responsável" in erro
    ok, erro, limpo = validar_campos(ac.campos_criacao(SETORES), {**base, "gerencia_interna": [gi.OPCOES[0]]})
    assert ok, erro
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), {**base, "gerencia_interna": [gi.OUTRA]})
    assert not ok and "gestor" in erro.lower()
```

(`_criacao_interno()` é o helper já existente em `tests/test_formularios_dinamicos.py:60`; como o campo novo é obrigatório, os testes antigos que usam esse helper e esperam `ok` passam a precisar de `gerencia_interna` — acrescente `"gerencia_interna": [gi.OPCOES[0]]` no dict base do helper e, no teste acima, remova a chave com `{k: v for k, v in _criacao_interno().items() if k != "gerencia_interna"}` para o caso "sem gerência".)

Em `tests/test_automacao.py`:

```python
def test_payload_interno_resolve_gestor_pela_gerencia():
    dados = {**DADOS_CRIACAO, "perfil": ac.PERFIL_INTERNO, "regiao_wmw": "",
             "gerencia_interna": "RH — Mariana Silva", "gestor_email": ""}
    p = dom.montar_payload(dom.TIPO_CRIACAO, dados, CHAMADO)
    assert p["gestor_email"] == "mariana.silva@bondmann.com.br"
    outra = {**dados, "gerencia_interna": gi.OUTRA, "gestor_email": "Chefe.X@bondmann.com.br"}
    assert dom.montar_payload(dom.TIPO_CRIACAO, outra, CHAMADO)["gestor_email"] == "chefe.x@bondmann.com.br"
```

(importar `from app.domain import gerencias_internas as gi` no topo.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formularios_dinamicos.py tests/test_automacao.py -p no:cacheprovider -k "obrigatorio_se or gerencia"`
Expected: FAIL — `TypeError: unexpected keyword argument 'obrigatorio_se'`.

- [ ] **Step 3: Write minimal implementation**

`app/domain/campos_dinamicos.py` — em `CampoDef`, depois de `visivel_se`:

```python
    # Obrigatório só quando o controlador tem um destes valores (ex.: e-mail do
    # gestor quando a gerência é "Outra"). Mesmo formato de ``visivel_se``.
    obrigatorio_se: tuple[str, tuple[str, ...]] | None = None
```

Em `validar_campos`, no bloco de campo normal, trocar `if campo.obrigatorio:` (dentro de `if not valor:`) por:

```python
            exigido = campo.obrigatorio or (
                campo.obrigatorio_se is not None
                and _valor_controlador(dados, campo.obrigatorio_se[0]) in campo.obrigatorio_se[1]
            )
            if exigido:
```

`app/domain/gerencias_internas.py`:

```python
"""Gerência responsável por colaborador interno → e-mail do gerente, que vira
o líder na UBD (plano v2, F4 — tabela da Seção 0, confirmada pelo gestor)."""

from __future__ import annotations

OUTRA = "Outra (informar o e-mail do gestor)"

GERENCIAS: tuple[tuple[str, str], ...] = (
    ("Compras — Alessandro Lodion", "alessandro@bondmann.com.br"),
    ("Controladoria — Anderson Viana", "anderson@bondmann.com.br"),
    ("PCP / Recebimento / Expedição — Elias Kirsten", "elias@bondmann.com.br"),
    ("Laboratório / Químico — Guilherme Rosa", "guilherme.rosa@bondmann.com.br"),
    ("RH — Mariana Silva", "mariana.silva@bondmann.com.br"),
    ("Comercial — Patricia Alves", "patricia.alves@bondmann.com.br"),
    ("Financeiro — Thiago Rodrigues", "thiago.rodrigues@bondmann.com.br"),
    ("Filial — Rogério Rossini", "rogerio@bondmann.com.br"),
)
OPCOES = tuple(r for r, _ in GERENCIAS) + (OUTRA,)


def email_do_gerente(rotulo: str | None) -> str | None:
    return dict(GERENCIAS).get((rotulo or "").strip())
```

`app/domain/formularios_acessos.py`: `from app.domain import gerencias_internas as gi`; no bloco "Colaborador Interno" de `campos_criacao`, antes de `cargo`:

```python
        CampoDef(
            "gerencia_interna", "Gerência responsável", "select", obrigatorio=True,
            opcoes=gi.OPCOES, visivel_se=_SO_INTERNO,
            ajuda="O gerente vira o líder do colaborador na UBD.",
        ),
```

e o `gestor_email` existente ganha `obrigatorio_se=("gerencia_interna", (gi.OUTRA,))` e ajuda "Obrigatório quando a gerência é \"Outra\". Usado como líder no UBD Learning.rocks.".

`app/domain/automacao.py`, em `montar_payload` CRIACAO, trocar a linha do `gestor_email` por:

```python
                "gestor_email": (
                    gi.email_do_gerente(dados.get("gerencia_interna"))
                    or str(dados.get("gestor_email") or "").strip().lower()
                    or None
                ),
```

(`from app.domain import gerencias_internas as gi` no topo.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_formularios_dinamicos.py tests/test_automacao.py tests/test_formularios_quimico.py tests/test_portal.py -p no:cacheprovider` e `ruff check app tests`
Expected: PASS.

- [ ] **Step 5: Commit** — mensagem `feat(automacao): gerencia responsavel do interno define o lider na UBD (v2 F4)`.

### Task 6: Worker — líderes resolvidos no SAP, confirmados na `regioes`, aplicados na UBD

**Files:**
- Create: `services/regioes.py`
- Modify: `config/settings.py` (`REGIOES_API_URL`, `REGIOES_API_KEY`), `services/sap_api.py` (`email_do_pn`, `lideres_por_regiao`, `gerentes_da_vaga`), `flow.py` (`_ubd` da criação)
- Test: `tests/test_lideranca.py` (novo)

**Interfaces:**
- Consumes: `find_region`, `find_regions_by_field`, `CAMPO_VAGA` (Task 2); `data.manager_email`, `data.region_code/region`, `data.vacancy_code`.
- Produces:
  - `SAPService.email_do_pn(card_code) -> Optional[str]` (None para marcador sem e-mail)
  - `SAPService.lideres_por_regiao(region_query) -> Dict[str, Optional[str]]` (só GET; roda também em dry-run) → `{"regiao": Code, "supervisor": email|None, "gerente": email|None}`
  - `SAPService.gerentes_da_vaga(vacancy_code) -> List[str]` (e-mails dos gerentes das regiões da equipe)
  - `RegioesService.linha(regiao_code) -> Optional[Dict[str, Any]]` (None = indisponível/sem linha)
  - `confirmar_lideres(sap: Dict, linha: Optional[Dict]) -> Tuple[str, str]` → (`"confirmado"|"nao_confirmado"|"divergente"`, texto)
  - `AutomationFlowOrchestrator._resolver_lideres(data) -> Tuple[List[str], List[str]]` (e-mails, avisos)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_lideranca.py
"""F4 da v2: líderes na UBD — resolvidos no SAP, confirmados na regioes (GET)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings  # noqa: E402
from flow import STEP_UBD_CRIACAO, AutomationFlowOrchestrator  # noqa: E402
from models import creation_data_from_payload  # noqa: E402
from services.regioes import RegioesService, confirmar_lideres  # noqa: E402

SAP_UBERLANDIA = {"regiao": "084-UBERLANDIA", "supervisor": "sup@bondmann.com.br", "gerente": "ger@bondmann.com.br"}


def test_confirmacao_concorda():
    linha = {"email_supervisor": "SUP@bondmann.com.br", "email_gerente": "ger@bondmann.com.br"}
    assert confirmar_lideres(SAP_UBERLANDIA, linha)[0] == "confirmado"


def test_confirmacao_sem_linha_e_nao_confirmado():
    assert confirmar_lideres(SAP_UBERLANDIA, None)[0] == "nao_confirmado"


def test_confirmacao_divergente_cita_os_dois_lados():
    estado, texto = confirmar_lideres(SAP_UBERLANDIA, {"email_supervisor": "outro@bondmann.com.br", "email_gerente": "ger@bondmann.com.br"})
    assert estado == "divergente" and "outro@bondmann.com.br" in texto and "sup@bondmann.com.br" in texto


def test_regioes_service_so_faz_get_e_trata_indisponivel(monkeypatch):
    chamadas = []

    class Resp:
        status_code = 200

        def json(self):
            return []

    def fake_get(url, headers=None, params=None, timeout=None):
        chamadas.append(("GET", url, params))
        return Resp()

    monkeypatch.setattr("services.regioes.requests.get", fake_get)
    svc = RegioesService(base_url="https://x.supabase.co", api_key="anon")
    assert svc.linha("084-UBERLANDIA") is None
    assert chamadas[0][2]["regiao"] == "eq.084-UBERLANDIA"
    assert RegioesService(base_url="", api_key="").linha("084-UBERLANDIA") is None  # sem env = desligado


def test_modulo_regioes_nao_escreve():
    fonte = Path(__file__).resolve().parent.parent.joinpath("services", "regioes.py").read_text(encoding="utf-8")
    for proibido in ("requests.post", "requests.patch", "requests.put", "requests.delete", ".post(", ".patch(", ".delete("):
        assert proibido not in fonte


class SapLideresStub:
    def lideres_por_regiao(self, q):
        return dict(SAP_UBERLANDIA)

    def gerentes_da_vaga(self, code):
        return ["ger@bondmann.com.br"]

    def login(self):
        return "s"

    def logout(self):
        return True


def _orq(monkeypatch, linha):
    monkeypatch.setattr(settings, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(settings, "validate_sap", lambda: True)
    orq = AutomationFlowOrchestrator(dry_run=False, interactive=False)
    orq.sap = SapLideresStub()
    orq.regioes = type("R", (), {"linha": lambda self, code: linha})()
    return orq


REP = {"nome_completo": "Rui Barros", "email": "rui.barros@bondmann.com.br", "telefone": "11999998888",
       "perfil": "REPRESENTANTE", "regiao": {"codigo": "084", "nome": "UBERLANDIA", "completo": "084-UBERLANDIA"},
       "dispositivo_wmw": "IOS"}


def test_representante_confirmado_recebe_supervisor_e_gerente(monkeypatch):
    orq = _orq(monkeypatch, {"email_supervisor": "sup@bondmann.com.br", "email_gerente": "ger@bondmann.com.br"})
    emails, avisos = orq._resolver_lideres(creation_data_from_payload(REP))
    assert emails == ["sup@bondmann.com.br", "ger@bondmann.com.br"] and avisos == []


def test_representante_divergente_nao_recebe_lider(monkeypatch):
    orq = _orq(monkeypatch, {"email_supervisor": "outro@bondmann.com.br", "email_gerente": "ger@bondmann.com.br"})
    emails, avisos = orq._resolver_lideres(creation_data_from_payload(REP))
    assert emails == [] and "diverge" in avisos[0]


def test_representante_sem_regioes_aplica_sap_e_avisa(monkeypatch):
    orq = _orq(monkeypatch, None)
    emails, avisos = orq._resolver_lideres(creation_data_from_payload(REP))
    assert emails == ["sup@bondmann.com.br", "ger@bondmann.com.br"] and "não confirmado" in avisos[0]


def test_supervisor_recebe_gerente_da_equipe_e_gerente_fica_sem_lider(monkeypatch):
    orq = _orq(monkeypatch, None)
    sup = {**REP, "perfil": "SUPERVISOR", "regiao": None, "vaga": {"tipo": "EQUIPE", "codigo": "RH2018", "nome": "EQUIPE SP 1"}}
    assert orq._resolver_lideres(creation_data_from_payload(sup))[0] == ["ger@bondmann.com.br"]
    ger = {**sup, "perfil": "GERENTE", "vaga": {"tipo": "GERENCIA", "codigo": "RH2005", "nome": "GERENTE SP"}}
    assert orq._resolver_lideres(creation_data_from_payload(ger)) == ([], [])


def test_interno_usa_o_gestor_do_payload(monkeypatch):
    orq = _orq(monkeypatch, None)
    interno = {"nome_completo": "Ana Lima", "email": "ana.lima@bondmann.com.br", "telefone": "11999998888",
               "perfil": "INTERNO", "cargo": "Assistente", "gestor_email": "mariana.silva@bondmann.com.br"}
    assert orq._resolver_lideres(creation_data_from_payload(interno)) == (["mariana.silva@bondmann.com.br"], [])


def test_ubd_novo_usuario_recebe_ids_dos_lideres(monkeypatch):
    monkeypatch.setattr(settings, "validate_learning_rocks", lambda: True)
    orq = _orq(monkeypatch, {"email_supervisor": "sup@bondmann.com.br", "email_gerente": "ger@bondmann.com.br"})
    ids = {"sup@bondmann.com.br": 40, "ger@bondmann.com.br": 41}
    criados = []
    monkeypatch.setattr(orq.learning_rocks, "find_user_by_email", lambda e: {"id": ids[e]} if e in ids else None)
    monkeypatch.setattr(orq.learning_rocks, "resolve_teams_by_job_title", lambda **k: ([66909], ["Bondmann"]))
    monkeypatch.setattr(orq.learning_rocks, "add_user_to_team", lambda t, u: True)

    def fake_create(**k):
        criados.append(k)
        return {"id": 99}

    monkeypatch.setattr(orq.learning_rocks, "create_user", fake_create)
    ubd = orq._etapa_ubd_criacao(creation_data_from_payload(REP), "rui.barros@bondmann.com.br", "rui.barros")
    assert criados[0]["leaders"] == [40, 41] and ubd["leaders_aplicados"] == ["sup@bondmann.com.br", "ger@bondmann.com.br"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_lideranca.py -q`
Expected: FAIL — `ModuleNotFoundError: services.regioes`.

- [ ] **Step 3: Write minimal implementation**

`config/settings.py`, depois do bloco do SAP:

```python
    # Tabela `regioes` (Supabase de outro projeto) — SOMENTE LEITURA, só para
    # confirmar a liderança resolvida no SAP (plano v2, F4). Vazio = desligado.
    REGIOES_API_URL: str = os.getenv("REGIOES_API_URL", "")
    REGIOES_API_KEY: str = os.getenv("REGIOES_API_KEY", "")
```

`services/regioes.py`:

```python
"""Leitura da tabela `regioes` (Supabase de outro projeto) — SOMENTE GET.

Regra dura do gestor (V1): este módulo nunca escreve nada nesse banco. A
liderança vem do SAP; aqui só se confirma (gestor, 2026-09-25)."""
from typing import Any, Dict, Optional, Tuple

import requests

from config import settings
from utils.logger import logger


class RegioesService:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (settings.REGIOES_API_URL if base_url is None else base_url).rstrip("/")
        self.api_key = settings.REGIOES_API_KEY if api_key is None else api_key

    def linha(self, regiao_code: str) -> Optional[Dict[str, Any]]:
        """Linha da região (``"084-UBERLANDIA"``) ou ``None`` (desligado, sem
        linha, RLS bloqueando ou erro — quem chama trata como "não confirmado")."""
        if not (self.base_url and self.api_key and regiao_code):
            return None
        try:
            resp = requests.get(
                f"{self.base_url}/rest/v1/regioes",
                headers={"apikey": self.api_key, "Authorization": f"Bearer {self.api_key}"},
                params={"select": "regiao,email_supervisor,email_gerente", "regiao": f"eq.{regiao_code}"},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.warning(f"regioes indisponível: {exc}")
            return None
        if resp.status_code != 200:
            logger.warning(f"regioes respondeu {resp.status_code}")
            return None
        itens = resp.json() or []
        return itens[0] if itens else None


def _norm(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def confirmar_lideres(sap: Dict[str, Optional[str]], linha: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    """Compara supervisor/gerente do SAP com a `regioes`."""
    if linha is None:
        return "nao_confirmado", f"liderança da região {sap.get('regiao')} não confirmada na regioes (sem leitura)"
    difs = []
    for chave, coluna in (("supervisor", "email_supervisor"), ("gerente", "email_gerente")):
        if _norm(sap.get(chave)) != _norm(linha.get(coluna)):
            difs.append(f"{chave}: SAP={sap.get(chave) or '—'} × regioes={linha.get(coluna) or '—'}")
    if difs:
        return "divergente", f"liderança da região {sap.get('regiao')} diverge entre SAP e regioes ({'; '.join(difs)})"
    return "confirmado", ""
```

`services/sap_api.py`, depois de `release_vacancy`:

```python
    def email_do_pn(self, card_code: Optional[str]) -> Optional[str]:
        """E-mail do PN (pessoa); marcadores de vaga não têm e-mail ⇒ None."""
        if not card_code:
            return None
        resp = self.session.get(
            f"{self.base_url}/BusinessPartners",
            params={"$filter": f"CardCode eq '{_odata(card_code)}'", "$select": "CardCode,EmailAddress"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"SAP respondeu {resp.status_code} ao ler o PN {card_code}: {resp.text[:300]}")
        itens = resp.json().get("value", [])
        email = (itens[0].get("EmailAddress") or "").strip().lower() if itens else ""
        return email or None

    def lideres_por_regiao(self, region_query: str) -> Dict[str, Optional[str]]:
        self.login()
        regiao = self.find_region(region_query)
        if not regiao:
            raise RuntimeError(f"Região '{region_query}' não encontrada na IB_CO_REGIAO")
        return {
            "regiao": regiao.get("Code"),
            "supervisor": self.email_do_pn(regiao.get("U_IB_CodCom3")),
            "gerente": self.email_do_pn(regiao.get("U_IB_CodCom4")),
        }

    def gerentes_da_vaga(self, vacancy_code: str) -> List[str]:
        """E-mails dos gerentes das regiões que hoje têm o marcador da equipe."""
        gerentes: List[str] = []
        for r in self.find_regions_by_field(CAMPO_VAGA["EQUIPE"], vacancy_code):
            email = self.email_do_pn(r.get("U_IB_CodCom4"))
            if email and email not in gerentes:
                gerentes.append(email)
        return gerentes
```

(`find_region` também precisa trazer `U_IB_CodCom4`: nos três `$select` de `find_region`, trocar `U_IB_CodCom1,U_IB_CodCom3` por `U_IB_CodCom1,U_IB_CodCom3,U_IB_CodCom4`.)

`flow.py`:
- import: `from services.regioes import RegioesService, confirmar_lideres`
- `__init__`: `self.regioes = RegioesService()`
- método novo na classe:

```python
    def _resolver_lideres(self, data: UserCreationData) -> Tuple[List[str], List[str]]:
        """Líderes (e-mails) do usuário novo na UBD + avisos para a nota.
        SAP é a fonte; a `regioes` confirma (plano v2, F4)."""
        perfil = data.profile_type
        if perfil in (UserProfileType.INTERNO, UserProfileType.COLABORADOR_INTERNO, UserProfileType.SDR):
            return ([data.manager_email] if data.manager_email else []), []
        if perfil == UserProfileType.GERENTE:
            return [], []
        # Leitura no SAP também na simulação (só GET) — a nota do dry-run mostra
        # os líderes previstos para a TI conferir antes da execução real.
        if not settings.validate_sap():
            return [], ["credenciais SAP ausentes — liderança não resolvida"]
        try:
            if perfil == UserProfileType.SUPERVISOR and data.vacancy_code:
                return self.sap.gerentes_da_vaga(data.vacancy_code), []
            if perfil == UserProfileType.REPRESENTANTE and (data.region_code or data.region):
                sap = self.sap.lideres_por_regiao(data.region or data.region_code)
                estado, texto = confirmar_lideres(sap, self.regioes.linha(sap["regiao"]))
                if estado == "divergente":
                    return [], [texto + " — liderança não aplicada; a sincronização corrige"]
                emails = [e for e in (sap.get("supervisor"), sap.get("gerente")) if e]
                return emails, ([texto] if texto else [])
        finally:
            self.sap.logout()
        return [], []
```

- a closure `_ubd` da criação vira o método `_etapa_ubd_criacao(self, data, target_email, mail_nick) -> Dict[str, Any]` (mesmo corpo de hoje) com a liderança; em `run_user_creation_flow`, a etapa passa a ser `self._run_step(results, STEP_UBD_CRIACAO, lambda: self._etapa_ubd_criacao(data, target_email, mail_nick))`. Corpo:

```python
    def _etapa_ubd_criacao(self, data: UserCreationData, target_email: str, mail_nick: str) -> Dict[str, Any]:
        team_ids, team_names = self.learning_rocks.resolve_teams_by_job_title(
            job_title=data.job_title,
            profile_type=data.profile_type.value if data.profile_type else None,
        )
        data.ubd_team_ids = team_ids
        data.ubd_team_names = team_names
        lideres, avisos = self._resolver_lideres(data)
        if self.dry_run:
            return {"learning_rocks_id": "DRY_RUN_12345", "status": "Simulado", "teams_assigned": team_names,
                    "team_ids": team_ids, "leaders_previstos": lideres, "avisos": avisos}
        if not settings.validate_learning_rocks():
            return {"note": "Token do Learning.rocks pendente no .env"}
        leader_ids: List[int] = []
        aplicados: List[str] = []
        for email in lideres:
            lider = self.learning_rocks.find_user_by_email(email)
            if lider and lider.get("id"):
                leader_ids.append(int(lider["id"]))
                aplicados.append(email)
            else:
                avisos.append(f"líder {email} não encontrado na UBD — aplicar manualmente")
        user_obj = self.learning_rocks.find_user_by_email(target_email)
        if user_obj:
            if lideres:
                avisos.append("usuário já existia na UBD — liderança não alterada (a sincronização aplica)")
            aplicados = []
        else:
            user_obj = self.learning_rocks.create_user(
                name=data.full_name, email=target_email, username=mail_nick, team_ids=team_ids,
                leaders=leader_ids or None,
            )
        user_id = user_obj.get("id") if user_obj else None
        if user_id:
            for t_id in team_ids:
                try:
                    self.learning_rocks.add_user_to_team(t_id, user_id)
                except Exception as err:
                    logger.warning(f"Aviso ao vincular time {t_id} para usuário {user_id}: {err}")
        return {"learning_rocks_user": user_obj, "teams_assigned": team_names, "team_ids": team_ids,
                "leaders_aplicados": aplicados, "avisos": avisos}
```

(importar `Tuple` de `typing` se ainda não estiver.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_lideranca.py tests/test_vagas.py tests/test_worker.py tests/test_sap_usuarios.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** — `git add services/regioes.py config/settings.py services/sap_api.py flow.py tests/test_lideranca.py`; mensagem `feat(ubd): lideres resolvidos no SAP, confirmados na regioes e aplicados na criacao (v2 F4)`.

### Task 7: Docs, deploy e homologação de F3/F4

**Files:**
- Modify (portal): `docs/automacao_api.md`, `docs/CHANGELOG.md`, `plano_md_mestre_automacao_acessos_v2.md` (Matriz; Seção 7 com o desvio "contrato v1 aditivo"; Seção 5 com "fonte SAP + confirmação regioes no worker")
- Modify (worker): `PLANO_MESTRE_AUTOMACAO.md` (v1.3.0), `docs/` se houver runbook de envs

- [ ] **Step 1: Contrato** — em `docs/automacao_api.md`: `perfil` aceita `GERENTE`; novo campo `vaga` `{tipo: "EQUIPE"|"GERENCIA", codigo, nome}` obrigatório para SUPERVISOR/GERENTE em CRIACAO e DESLIGAMENTO (`regiao` = null para eles); `gestor_email` passa a vir da "Gerência responsável"; etapas novas `SAP Business One (Service Layer) - Ocupar Vaga (Equipe/Gerência)` e `… - Devolver Vaga (Equipe/Gerência)`; detalhes da etapa UBD ganham `leaders_aplicados` e `avisos`. Nota: contrato continua v1 (aditivo); deploy worker antes do portal.
- [ ] **Step 2: Suítes** — worker: `python -m pytest tests/test_lideranca.py tests/test_vagas.py tests/test_worker.py tests/test_sap_usuarios.py tests/test_sap_internal_user.py -q`; portal: `npm run build:css && python -m pytest -p no:cacheprovider && ruff check app tests`. Tudo verde.
- [ ] **Step 3: Matriz e CHANGELOG** — F3/F4 → 🟡 Em processo; linha no CHANGELOG com o resumo desta entrega. Commit nos dois repos.
- [ ] **Step 4 (gestor): envs e deploy** — no serviço `automacao-worker` do Railway: `REGIOES_API_URL=https://zboazezxqkieryaasyuc.supabase.co` e `REGIOES_API_KEY=<chave anon fornecida pelo gestor>`; pedir ao dono do projeto `regioes` a política de SELECT para `anon` (sem ela o worker aplica com aviso "não confirmado"). Push/merge **primeiro do worker**, depois do portal.
- [ ] **Step 5 (gestor): homologação**
  1. GET da `IB_CO_REGIAO` guardado (script `f0b_sap_regiao.py`).
  2. Dry-run de criação de supervisor na equipe `RH2021 EQUIPE MG 2` (vaga aberta hoje em 6 regiões) ⇒ nota mostra a vaga e os `leaders_previstos`.
  3. Real com um PN de teste cadastrado pelo comercial: ocupar e depois devolver a vaga ⇒ `IB_CO_REGIAO` final idêntica à guardada.
  4. Criação real de um representante de teste ⇒ na UBD (Configurações do usuário → Liderança) aparecem supervisor e gerente da região ⇒ desligar o usuário de teste.

---

## F5.0 — Spike da API de liderança da UBD (decide o plano da F5)

### Task 8: Descobrir a semântica do `PATCH leaders` e onde ler os líderes atuais

**Files:**
- Create (descartável): `<scratchpad>/f5/spike_ubd_leaders.py`
- Modify: `plano_md_mestre_automacao_acessos_v2.md` (Seção 6: resultado do spike e desenho escolhido)

**Pré-requisito (gestor):** um usuário de teste na UBD e dois líderes de teste (IDs). Toda escrita deste spike é só nesse usuário de teste e com o "ok" do gestor na hora.

- [ ] **Step 1: Script do spike**

```python
# spike_ubd_leaders.py — F5.0. Escreve SÓ no usuário de teste indicado pelo gestor.
import os, sys, json, requests
sys.path.insert(0, os.getcwd())
from services.learning_rocks import LearningRocksService

lr = LearningRocksService()
teste_id, lider_a, lider_b = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
url = f"{lr.base_url}/workspace/v1/users/{teste_id}"
for passo, corpo in (("A", {"leaders": [lider_a]}), ("B", {"leaders": [lider_b]})):
    r = requests.patch(url, headers=lr._headers(), json=corpo, timeout=20)
    print(passo, r.status_code, r.text[:500])
    input(f"Confira na UBD (Configurações do usuário → Liderança) e tecle Enter [após {passo}]: ")
for rota in ("", "/leaders", "/leadership"):
    r = requests.get(url + rota, headers=lr._headers(), timeout=20)
    print("GET", rota or "/", r.status_code, json.dumps(r.json(), ensure_ascii=False)[:800] if r.ok else r.text[:200])
r = requests.get(f"{lr.base_url}/workspace/v2/users", headers=lr._headers(), params={"id__eq": teste_id}, timeout=20)
print("LIST v2", r.status_code, r.text[:800])
```

- [ ] **Step 2 (gestor presente): rodar** — `cd "C:/Users/Osvaldo/Downloads/Automação" && python <scratchpad>/f5/spike_ubd_leaders.py <id_teste> <id_lider_a> <id_lider_b>`; o gestor diz o que a tela da UBD mostra depois de A e depois de B.
- [ ] **Step 3: Registrar e decidir** — na Seção 6 da spec, uma de três conclusões:
  - **PATCH acumula** (depois de B aparecem A e B) ⇒ F5 só adiciona; remoção de supervisor desligado vira endpoint próprio (se existir) ou ação manual avisada.
  - **PATCH substitui e há leitura** em alguma rota ⇒ desenho original (espelho com proteção, V4).
  - **PATCH substitui e não há leitura** ⇒ o portal guarda o último conjunto aplicado por usuário (tabela nova) e a F5 só mexe em quem ela mesma configurou; líderes manuais em usuários nunca tocados ficam preservados; decisão V4 revista com o gestor.
- [ ] **Step 4: Plano da F5** — escrever `docs/superpowers/plans/<data>-automacao-acessos-v2-f5.md` com o desenho escolhido (migration `0092`, gatilhos diário 06h e por evento, `run_leadership_sync_flow`, modo relatório → aplicar). Commit da spec.

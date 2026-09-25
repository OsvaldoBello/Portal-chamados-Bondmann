# Automação de Acessos v2 — F0, F1 e F2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Responder o levantamento somente-leitura (F0), corrigir a busca/bloqueio/criação de usuário interno no SAP que falhou no desligamento de Paola Kemel (F1) e entregar a caixa "Urgência" + horário padrão 17h no desligamento (F2).

**Architecture:** F0 são scripts descartáveis no scratchpad que só fazem GET/SELECT e gravam as respostas no plano mestre v2. F1 mexe só no worker (`C:\Users\Osvaldo\Downloads\Automação`, repo `OsvaldoBello/Bondmann-automacao-acessos`, branch base `master`): `services/sap_api.py` passa a consultar `Users` por `$filter` e a escrever por `InternalKey`. F2 mexe só no portal (este repo): novo tipo de campo `checkbox` no motor de formulários, campo `urgente` no layout de desligamento, payload e agendamento (`calcular_executar_apos`), selo no card.

**Tech Stack:** Python 3.12, FastAPI + Jinja2 (portal), pytest, `requests` + `http.server` (fake da Service Layer nos testes do worker), SAP B1 Service Layer (OData v3), MS Graph, Skore/UBD.

**Spec:** [`plano_md_mestre_automacao_acessos_v2.md`](../../../plano_md_mestre_automacao_acessos_v2.md) (Seções 0–3 e 8). Plano-pai: [`plano_md_mestre_automacao_acessos.md`](../../../plano_md_mestre_automacao_acessos.md).

**Escopo:** este plano cobre F0, F1 e F2. F3–F5 ganham plano próprio **depois** que a F0 estiver respondida (colunas da `regioes`, `CodCom2`, API de líderes da UBD), para não haver lacunas.

## Global Constraints

- **Banco da `regioes` (Supabase de outro projeto) é SOMENTE LEITURA:** nenhum INSERT/UPDATE/DELETE/DDL/UPSERT, nem em teste (V1).
- **SAP na F0:** só GET, pela Service Layer da automação (`SAPService`, via túnel) (V8). Nenhum PATCH/POST em sistema externo fora da homologação autorizada pelo gestor.
- **Nomes de etapa (`step_name`) são contrato** com o portal — não renomear.
- **Não commitar segredos**; `.env` fica fora do git nos dois repos.
- **Worker:** existe uma alteração local NÃO commitada em `services/ms_graph.py` (limpeza de regras de encaminhamento anteriores) que **não é deste plano** — nunca usar `git add -A`/`git add .` no repo do worker; adicionar arquivos pelo nome e perguntar ao gestor o destino dessa alteração.
- **Portal:** `app/static/css/app.css` é gerado (`npm run build:css`) e gitignored; rodar antes do pytest completo.
- **Erro crítico aborta o fluxo** e usuário SAP inexistente no desligamento = etapa ✅ "nada a bloquear" (regras de 2026-09-15, inalteradas).
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Idioma de código/comentários/mensagens: português, no estilo dos arquivos vizinhos.

## File Structure

| Arquivo | Repo | Responsabilidade |
|---|---|---|
| `<scratchpad>/f0/*.py` | — (descartável) | Consultas somente-leitura da F0 |
| `plano_md_mestre_automacao_acessos_v2.md` | portal | Respostas da F0 (Seção 1) + matriz de estados |
| `services/sap_api.py` | worker | Helpers puros de nome/código SAP; `find_internal_user`, `deactivate_internal_user`, `create_internal_user` reescritos |
| `flow.py` | worker | Passa `full_name` ao bloqueio SAP |
| `models/factories.py` | worker | `sap_user_code` via `derivar_user_code` |
| `tests/test_sap_usuarios.py` (novo) | worker | Fake da Service Layer em HTTP real + testes de F1 |
| `PLANO_MESTRE_AUTOMACAO.md`, `docs/…` | worker | Histórico da v1.2 |
| `app/domain/campos_dinamicos.py` | portal | Tipo `checkbox` |
| `app/templates/portal/_campos_dinamicos.html` | portal | Render do `checkbox` |
| `app/domain/formularios_acessos.py` | portal | Campo `urgente` no desligamento |
| `app/domain/automacao.py` | portal | `urgente` no payload, agendamento 17h/imediato, resumo |
| `app/config.py`, `.env.example` | portal | Default 17h |
| `app/services/automacao.py`, `app/templates/workspace/atendimento.html` | portal | `CardAutomacao.urgente`, selo e rótulo do botão |
| `tests/test_formularios_dinamicos.py`, `tests/test_automacao.py` | portal | Testes de F2 |
| `docs/automacao_api.md`, `docs/CHANGELOG.md`, `plano_md_mestre_automacao_acessos.md` | portal | Docs de F2 |

---

## F0 — Levantamento (somente leitura)

### Task 1: Consultas da F0 e respostas no plano mestre v2

**Files:**
- Create (descartáveis, fora dos repos): `C:\Users\Osvaldo\AppData\Local\Temp\claude\C--Users-Osvaldo-Desktop-Portal-chamados-Bondmann-claude-develop\01c8e3d9-6177-4ee8-9571-02af45f37436\scratchpad\f0\f0_sap.py`, `f0_regioes.py`, `f0_graph.py`, `f0_ubd.py`
- Modify: `plano_md_mestre_automacao_acessos_v2.md` (Seção 1 e Matriz de fases)

**Interfaces:**
- Consumes: `SAPService`, `MSGraphService`, `LearningRocksService` e `config.settings` do worker (credenciais do `.env` local do worker, nunca impressas).
- Produces: respostas #1–#7 da Seção 1 do plano v2 — entrada obrigatória do plano de F3–F5.

Pré-requisito: rodar na máquina que é o subnet router do Tailscale (alcança `10.151.4.40`), com o `.env` do worker presente em `C:\Users\Osvaldo\Downloads\Automação\.env`. Se `SAP_PROXY_URL` estiver preenchido no `.env` local, sobrescrever para vazio via variável de ambiente no comando (acesso direto).

- [ ] **Step 1: Script SAP (#2, #3, #4) — só GET**

```python
# f0_sap.py — F0 #2/#3/#4. SOMENTE GET. Rodar com cwd = pasta do worker.
import json, os, sys
os.environ["SAP_PROXY_URL"] = ""
sys.path.insert(0, os.getcwd())
from services.sap_api import SAPService

sap = SAPService()
sap.login()
try:
    # #2 — como estão os campos de comissão das regiões hoje
    regioes = sap.get_all_regions()
    print(f"[#2] {len(regioes)} regiões")
    for campo in ("U_IB_CodCom1", "U_IB_CodCom2", "U_IB_CodCom3"):
        valores = {}
        for r in regioes:
            valores[r.get(campo) or "(vazio)"] = valores.get(r.get(campo) or "(vazio)", 0) + 1
        top = sorted(valores.items(), key=lambda kv: -kv[1])[:15]
        print(f"[#2] {campo}: {top}")
    print("[#2] amostra:", json.dumps(regioes[:5], ensure_ascii=False))

    # #3 — PNs RH* (equipes, gerências, RH2020)
    url = f"{sap.base_url}/BusinessPartners"
    params = {"$filter": "startswith(CardCode,'RH')", "$select": "CardCode,CardName,CardForeignName,Valid,Frozen", "$orderby": "CardCode"}
    itens, prox = [], None
    while True:
        r = sap.session.get(prox or url, params=None if prox else params, timeout=15)
        r.raise_for_status()
        corpo = r.json()
        itens += corpo.get("value", [])
        prox = corpo.get("odata.nextLink")
        if not prox:
            break
        prox = prox if prox.startswith("http") else f"{sap.base_url}/{prox.lstrip('/')}"
    for i in itens:
        print("[#3]", i.get("CardCode"), "|", i.get("CardName"), "| fantasia:", i.get("CardForeignName"), "| Valid:", i.get("Valid"), "| Frozen:", i.get("Frozen"))

    # #4 — usuário da Paola e padrão de códigos
    sel = "InternalKey,UserCode,UserName,eMail,Locked"
    r = sap.session.get(f"{sap.base_url}/Users", params={"$filter": "UserCode eq 'PAOLAK'", "$select": sel}, timeout=10)
    print("[#4] PAOLAK:", r.status_code, r.text[:500])
    r = sap.session.get(f"{sap.base_url}/Users", params={"$select": sel, "$orderby": "InternalKey desc", "$top": 15}, timeout=10)
    print("[#4] últimos usuários:", r.status_code, r.text[:3000])
finally:
    sap.logout()
```

Run: `cd "C:/Users/Osvaldo/Downloads/Automação" && python "<scratchpad>/f0/f0_sap.py"`
Expected: listagens sem erro. Se `$orderby`/`$top` não forem aceitos, remover e repetir. Registrar: qual campo tem códigos `RH2005/RH2033` (gerência) e qual tem `RH20xx`/PN de pessoa (supervisor); lista completa de equipes/gerências; `InternalKey`, `UserName` e `eMail` de `PAOLAK`; regra de código observada nos internos recentes.

- [ ] **Step 2: Script `regioes` (#1) — só SELECT via PostgREST**

```python
# f0_regioes.py — F0 #1. SOMENTE GET no PostgREST do outro projeto (V1: nunca escrever).
import re, sys, json, pathlib, requests

txt = pathlib.Path(r"C:\Users\Osvaldo\Downloads\SUPABASE representantes.txt").read_text(encoding="utf-8")
env = dict(re.findall(r"^\s*([A-Z_]+)\s*=\s*(\S+)\s*$", txt, re.M))
base = env["SUPABASE_URL"].rstrip("/") + "/rest/v1"
chave = env.get("SUPABASE_ANON_KEY") or env["SUPABASE_KEY"]  # preferir a anon
h = {"apikey": chave, "Authorization": f"Bearer {chave}"}

# Colunas pela definição OpenAPI do PostgREST (sem imprimir a chave)
spec = requests.get(base + "/", headers=h, timeout=20)
print("[#1] openapi:", spec.status_code)
if spec.ok:
    defs = spec.json().get("definitions", {})
    print("[#1] tabelas:", sorted(defs))
    print("[#1] colunas regioes:", json.dumps(defs.get("regioes", {}).get("properties", {}), ensure_ascii=False, indent=1))
r = requests.get(base + "/regioes", headers={**h, "Prefer": "count=exact"}, params={"select": "*", "limit": "5"}, timeout=20)
print("[#1] status:", r.status_code, "| total:", r.headers.get("Content-Range"))
print("[#1] amostra:", json.dumps(r.json(), ensure_ascii=False, indent=1)[:4000])
```

Run: `python "<scratchpad>/f0/f0_regioes.py"`
Expected: 200 com colunas e amostra. Se a *anon* devolver 401/`[]` por RLS, repetir com `SUPABASE_KEY` e **registrar no plano** que só a chave de serviço lê (risco: chave com poder de escrita — a F4 deve pedir ao dono do projeto uma role/visão só de leitura). Registrar: coluna que identifica o representante (e-mail/CardCode), colunas de e-mail do supervisor e do gerente, coluna de código da região, se há `updated_at`/`created_at` e o intervalo típico entre atualizações (→ `AUTOMACAO_SYNC_ATRASO_MIN`).

- [ ] **Step 3: Script Graph (#5, #6) — só GET**

```python
# f0_graph.py — F0 #5/#6. SOMENTE GET no MS Graph. Rodar com cwd = pasta do worker.
import os, sys, requests
sys.path.insert(0, os.getcwd())
from services.ms_graph import MSGraphService

g = MSGraphService()
h = {**g._headers(), "ConsistencyLevel": "eventual"}
nomes = ["Alessandro Lodion", "Anderson Viana", "Elias Kiesten", "Guilherme Rosa",
         "Mariana Silva", "Patricia Alves", "Thiago Rodrigues", "Rogério Rossini"]
for nome in nomes:
    primeiro = nome.split()[0]
    r = requests.get(f"{g.graph_url}/users", headers=h, timeout=20, params={
        "$search": f'"displayName:{primeiro}"', "$select": "displayName,mail,userPrincipalName,accountEnabled,department,jobTitle", "$top": "25"})
    achados = [u for u in r.json().get("value", []) if nome.split()[-1].lower()[:4] in (u.get("displayName") or "").lower()]
    print("[#5]", nome, "→", [(u["displayName"], u.get("mail") or u["userPrincipalName"], u.get("accountEnabled")) for u in achados] or f"NADA (status {r.status_code})")
for termo in ("geren", "Geren", "supervis"):
    r = requests.get(f"{g.graph_url}/groups", headers=h, timeout=20, params={
        "$search": f'"displayName:{termo}"', "$select": "id,displayName,mail,groupTypes,mailEnabled,securityEnabled"})
    for grp in r.json().get("value", []):
        print("[#6]", grp["id"], "|", grp["displayName"], "|", grp.get("mail"), "|", grp.get("groupTypes"))
```

Run: `cd "C:/Users/Osvaldo/Downloads/Automação" && python "<scratchpad>/f0/f0_graph.py"`
Expected: um e-mail por gerente (ambíguos listados) e os grupos de Gerência/Supervisão com IDs.

- [ ] **Step 4: Script UBD (#7) — só GET**

```python
# f0_ubd.py — F0 #7. SOMENTE GET na UBD (Skore). Rodar com cwd = pasta do worker.
import os, sys, json, requests
sys.path.insert(0, os.getcwd())
from services.learning_rocks import LearningRocksService

lr = LearningRocksService()
email = sys.argv[1]  # e-mail de um representante que JÁ tem liderança na UBD (o gestor indica)
u = lr.find_user_by_email(email)
print("[#7] v2/users (chaves):", sorted(u.keys()) if u else None)
print("[#7] v2/users (liderança):", {k: v for k, v in (u or {}).items() if "lead" in k.lower() or "lider" in k.lower() or "manager" in k.lower()})
if u:
    for url in (f"{lr.base_url}/workspace/v1/users/{u['id']}", f"{lr.base_url}/workspace/v2/users/{u['id']}"):
        r = requests.get(url, headers=lr._headers(), timeout=20)
        print("[#7] GET", url.split('/workspace')[1], r.status_code, json.dumps(r.json(), ensure_ascii=False)[:2500] if r.ok else r.text[:300])
```

Run: `cd "C:/Users/Osvaldo/Downloads/Automação" && python "<scratchpad>/f0/f0_ubd.py" <email-de-representante-com-lider>`
Expected: descobrir em que campo vêm os líderes (e se são e-mails ou IDs). Consultar também a coleção Postman já usada no projeto (`tests/parse_skore_postman_doc.py` aponta para ela) e a doc pública do Skore para o `PATCH /workspace/v1/users/{id}` com `leaders`. **Não executar PATCH.** Registrar: formato de `leaders` no POST, como ler, e se existe atualização de líderes de usuário existente.

- [ ] **Step 5: Gravar as respostas no plano v2**

Em `plano_md_mestre_automacao_acessos_v2.md`, Seção 1: acrescentar abaixo da tabela um bloco `### Respostas (2026-MM-DD)` com uma linha por item #1–#7 no formato `**#N** — resposta objetiva · fonte (script/GET)`. Preencher a tabela de gerentes internos da Seção 0 com os e-mails **somente após o gestor confirmar** a lista do Step 3. Se #2 contradisser V3 (ex.: `CodCom2` não é gerente) ou #7 mostrar que não há como alterar líderes de usuário existente, escrever isso em destaque ⚠️ e parar para decisão do gestor antes de F3/F5. Na Matriz: itens F0 #1–#7 → 🟢 Pronto; linha F0 → 🟢 Pronto; "Atualizado em" = data do dia.

- [ ] **Step 6: Commit**

```bash
git add plano_md_mestre_automacao_acessos_v2.md
git commit -m "docs(automacao): respostas da F0 (levantamento somente leitura)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## F1 — SAP: usuário interno (worker)

Todas as tasks de F1 rodam em `C:\Users\Osvaldo\Downloads\Automação`, numa branch nova:

```bash
cd "C:/Users/Osvaldo/Downloads/Automação" && git checkout master && git checkout -b fix/sap-usuario-interno
```

### Task 2: Helpers puros de nome e código SAP

**Files:**
- Modify: `services/sap_api.py` (topo do módulo, depois de `DEFAULT_TOTAL_LICENSES`)
- Test: `tests/test_sap_usuarios.py` (novo)

**Interfaces:**
- Produces (módulo `services.sap_api`):
  - `_sem_acentos(texto: str) -> str`
  - `_nome_sap(full_name: str) -> str` — maiúsculas, espaços colapsados, acentos preservados
  - `_odata(valor: str) -> str` — escapa `'` → `''`
  - `derivar_user_code(full_name: str) -> str` — `"Paola Kemel"` → `"PAOLAK"`
  - `candidatos_user_code(full_name: str, user_code: str | None = None) -> list[str]`
  - `class SAPUsuarioAmbiguo(LookupError)`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sap_usuarios.py
"""Usuário interno no SAP (F1 da v2 — bug do desligamento de Paola Kemel, 2026-09-24).

Service Layer falsa em HTTP real: responde 400/201 a `Users('<texto>')` exatamente
como o SAP real, então qualquer regressão para chave-texto quebra aqui.
Rodar: ``python -m pytest tests/test_sap_usuarios.py -q``.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.sap_api import (  # noqa: E402
    _nome_sap,
    _odata,
    candidatos_user_code,
    derivar_user_code,
)


@pytest.mark.parametrize(
    "nome, esperado",
    [
        ("Paola Kemel", "PAOLAK"),
        ("  paola   kemel ", "PAOLAK"),
        ("João da Silva", "JOAOS"),
        ("Ana", "ANA"),
        ("Maria D'Ávila", "MARIAD"),
    ],
)
def test_derivar_user_code(nome, esperado):
    assert derivar_user_code(nome) == esperado


def test_candidatos_user_code_estende_o_sobrenome():
    assert candidatos_user_code("Paola Kemel") == ["PAOLAK", "PAOLAKE", "PAOLAKEM", "PAOLAKEME", "PAOLAKEMEL"]
    assert candidatos_user_code("Paola Kemel", "pk1")[:2] == ["PK1", "PAOLAK"]
    assert candidatos_user_code("Ana") == ["ANA"]


def test_nome_sap_e_escape_odata():
    assert _nome_sap("  Paola   Kemel ") == "PAOLA KEMEL"
    assert _nome_sap("João da Silva") == "JOÃO DA SILVA"
    assert _odata("ANA D'AVILA") == "ANA D''AVILA"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sap_usuarios.py -q`
Expected: FAIL — `ImportError: cannot import name '_nome_sap'`

- [ ] **Step 3: Write minimal implementation**

Em `services/sap_api.py`, trocar o bloco da exceção existente e acrescentar os helpers (adicionar `import unicodedata` junto aos imports do topo):

```python
class SAPUsuarioNaoEncontrado(LookupError):
    """Nenhum usuário SAP (OUSR) com o código nem com o e-mail informados."""


class SAPUsuarioAmbiguo(LookupError):
    """Mais de um usuário SAP casa com o critério — nunca bloquear no escuro."""


# Campos lidos de /Users. A chave da entidade na Service Layer é InternalKey
# (inteiro): `Users('<texto>')` responde 400/201 "key value is not matched with
# its type" — foi o que derrubou o desligamento de Paola Kemel (2026-09-24).
# Toda busca é por coleção com $filter; toda escrita é Users(<InternalKey>).
_USER_SELECT = "InternalKey,UserCode,UserName,eMail,Locked"


def _sem_acentos(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


def _nome_sap(full_name: str) -> str:
    """Como o SAP guarda o nome do usuário: maiúsculas, espaços simples."""
    return " ".join((full_name or "").split()).upper()


def _odata(valor: str) -> str:
    """Escapa aspas simples para literal OData ('' dentro de '...')."""
    return valor.replace("'", "''")


def _partes_nome(full_name: str) -> list[str]:
    return _sem_acentos(full_name or "").upper().replace("'", "").split()


def derivar_user_code(full_name: str) -> str:
    """Padrão Bondmann: PRIMEIRONOME + inicial do último sobrenome (``PAOLAK``)."""
    partes = _partes_nome(full_name)
    if not partes:
        return ""
    return partes[0] + (partes[-1][0] if len(partes) > 1 else "")


def candidatos_user_code(full_name: str, user_code: Optional[str] = None) -> List[str]:
    """Códigos a tentar na criação, em ordem: o explícito (se veio), depois o
    padrão e, em colisão, mais letras do último sobrenome (PAOLAK, PAOLAKE, …)."""
    candidatos: List[str] = []
    if user_code and user_code.strip():
        candidatos.append(user_code.strip().upper())
    partes = _partes_nome(full_name)
    if len(partes) == 1:
        candidatos.append(partes[0])
    elif partes:
        candidatos += [partes[0] + partes[-1][:i] for i in range(1, len(partes[-1]) + 1)]
    return list(dict.fromkeys(candidatos))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sap_usuarios.py -q`
Expected: PASS (7 testes)

- [ ] **Step 5: Commit**

```bash
git add services/sap_api.py tests/test_sap_usuarios.py
git commit -m "feat(sap): helpers de nome e codigo de usuario SAP (PRIMEIRONOME+inicial)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 3: `find_internal_user` por `$filter` (com fake da Service Layer)

**Files:**
- Modify: `services/sap_api.py` — substituir `find_internal_user_code` (linhas ~706–729) por `_users_where` + `find_internal_user`
- Test: `tests/test_sap_usuarios.py`

**Interfaces:**
- Consumes: helpers da Task 2.
- Produces:
  - `SAPService._users_where(filtro: str) -> list[dict]` — `GET /Users?$filter=…&$select=_USER_SELECT`; status ≠ 200 ⇒ `RuntimeError("SAP respondeu <status> ao buscar Users [<filtro>]: <texto>")`
  - `SAPService.find_internal_user(full_name: str, email: Optional[str] = None, user_code: Optional[str] = None) -> Optional[dict]` — dict com `InternalKey, UserCode, UserName, eMail, Locked`; `SAPUsuarioAmbiguo` quando um critério casa com >1
  - Fixture de teste `sap` (fábrica `(usuarios, falhar_get=None) -> (FakeSAP, SAPService)`)

- [ ] **Step 1: Write the failing test**

Acrescentar a `tests/test_sap_usuarios.py` (imports no topo do arquivo, junto aos existentes):

```python
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from config import settings  # noqa: E402
import services.sap_api as sap_api  # noqa: E402
from services.sap_api import SAPService, SAPUsuarioAmbiguo  # noqa: E402

_FILTRO_RE = re.compile(r"^(tolower\(eMail\)|UserCode|UserName) eq '((?:[^']|'')*)'$")


def _casa(filtro: str, u: dict) -> bool:
    m = _FILTRO_RE.match(filtro)
    assert m, f"filtro inesperado: {filtro!r}"
    campo, valor = m.group(1), m.group(2).replace("''", "'")
    if campo == "tolower(eMail)":
        return (u.get("eMail") or "").lower() == valor
    return (u.get(campo) or "") == valor


class FakeSAP:
    """Service Layer mínima: Login, GET /Users?$filter, PATCH Users(<int>), POST /Users.
    `Users('<texto>')` responde 400/201 como o SAP real."""

    def __init__(self, usuarios, falhar_get=None):
        self.usuarios = [dict(u) for u in usuarios]
        self.falhar_get = falhar_get
        self.gets: list[str] = []
        self.patches: list[tuple[int, dict]] = []
        self.posts: list[dict] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
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

            def _erro_chave(self):
                self._send(400, {"error": {"code": 201, "message": {
                    "lang": "en-us", "value": "Invalid query option: key value is not matched with its type"}}})

            def do_POST(self):
                path = urlsplit(self.path).path
                if path.endswith("/Login"):
                    return self._send(200, {"SessionId": "sess-teste"})
                if path.endswith("/Logout"):
                    return self._send(204)
                if path.endswith("/Users"):
                    body = self._body()
                    fake.posts.append(body)
                    chave = max([u["InternalKey"] for u in fake.usuarios] or [0]) + 1
                    fake.usuarios.append({**body, "InternalKey": chave})
                    return self._send(201, {**body, "InternalKey": chave})
                self._send(404, {})

            def do_GET(self):
                partes = urlsplit(self.path)
                fake.gets.append(unquote(self.path))
                if "/Users(" in partes.path:
                    return self._erro_chave()
                if partes.path.endswith("/Users"):
                    if fake.falhar_get:
                        return self._send(fake.falhar_get, {"error": {"message": {"value": "falha simulada"}}})
                    filtro = parse_qs(partes.query).get("$filter", [""])[0]
                    return self._send(200, {"value": [u for u in fake.usuarios if _casa(filtro, u)]})
                self._send(404, {})

            def do_PATCH(self):
                m = re.search(r"/Users\((\d+)\)$", urlsplit(self.path).path)
                if not m:
                    return self._erro_chave()
                chave, body = int(m.group(1)), self._body()
                fake.patches.append((chave, body))
                for u in fake.usuarios:
                    if u["InternalKey"] == chave:
                        u.update(body)
                self._send(204)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/b1s/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


PAOLA = {"InternalKey": 57, "UserCode": "PAOLAK", "UserName": "PAOLA KEMEL", "eMail": "", "Locked": "tNO"}


@pytest.fixture
def sap(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "SAP_PROXY_URL", "", raising=False)
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sap_api, "LICENSES_FILE", tmp_path / "sap_licenses.json")
    fakes: list[FakeSAP] = []

    def _fabrica(usuarios, falhar_get=None):
        fake = FakeSAP(usuarios, falhar_get=falhar_get)
        fakes.append(fake)
        return fake, SAPService(base_url=fake.url, company_db="SBO_TESTE", username="u", password="p")

    yield _fabrica
    for f in fakes:
        f.close()


def test_acha_paola_pelo_codigo_derivado_sem_usar_chave_texto(sap):
    fake, svc = sap([PAOLA])
    u = svc.find_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br")
    assert u["InternalKey"] == 57 and u["UserCode"] == "PAOLAK"
    assert not any("Users('" in g for g in fake.gets)


def test_codigo_derivado_de_outra_pessoa_nao_casa(sap):
    _, svc = sap([{**PAOLA, "UserName": "PAOLA KRUGER"}])
    assert svc.find_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br") is None


def test_acha_pelo_email_quando_codigo_e_outro(sap):
    _, svc = sap([{**PAOLA, "UserCode": "PKEMEL", "UserName": "PAOLA K.", "eMail": "Paola.Kemel@bondmann.com.br"}])
    assert svc.find_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br")["UserCode"] == "PKEMEL"


def test_acha_pelo_nome_sem_acentos(sap):
    _, svc = sap([{"InternalKey": 9, "UserCode": "XYZ", "UserName": "JOAO DA SILVA", "eMail": "", "Locked": "tNO"}])
    assert svc.find_internal_user("João da Silva")["InternalKey"] == 9


def test_nome_com_apostrofo_e_escapado(sap):
    _, svc = sap([{"InternalKey": 3, "UserCode": "X1", "UserName": "ANA D'AVILA", "eMail": "", "Locked": "tNO"}])
    assert svc.find_internal_user("Ana D'Avila")["InternalKey"] == 3


def test_nome_ambiguo_levanta_com_os_codigos(sap):
    _, svc = sap([
        {"InternalKey": 1, "UserCode": "AAA", "UserName": "PAOLA KEMEL", "eMail": "", "Locked": "tNO"},
        {"InternalKey": 2, "UserCode": "BBB", "UserName": "PAOLA KEMEL", "eMail": "", "Locked": "tNO"},
    ])
    with pytest.raises(SAPUsuarioAmbiguo) as exc:
        svc.find_internal_user("Paola Kemel")
    assert "AAA" in str(exc.value) and "BBB" in str(exc.value)


def test_erro_http_da_service_layer_vira_runtimeerror(sap):
    _, svc = sap([PAOLA], falhar_get=500)
    with pytest.raises(RuntimeError, match="500"):
        svc.find_internal_user("Paola Kemel")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sap_usuarios.py -q`
Expected: FAIL — `AttributeError: 'SAPService' object has no attribute 'find_internal_user'`

- [ ] **Step 3: Write minimal implementation**

Em `services/sap_api.py`, apagar o método `find_internal_user_code` inteiro e colocar no lugar:

```python
    def _users_where(self, filtro: str) -> List[Dict[str, Any]]:
        """GET /Users com $filter (nunca Users('<texto>') — ver _USER_SELECT)."""
        resp = self.session.get(
            f"{self.base_url}/Users",
            params={"$filter": filtro, "$select": _USER_SELECT},
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"SAP respondeu {resp.status_code} ao buscar Users [{filtro}]: {resp.text[:300]}")
        return resp.json().get("value", [])

    def find_internal_user(
        self, full_name: str, email: Optional[str] = None, user_code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Usuário interno (OUSR) do colaborador, ou ``None``.

        Ordem: código (explícito e ``derivar_user_code``) — só aceito se o
        ``UserName`` for o do colaborador, para nunca pegar um homônimo de
        código (PAOLAK de Paola Kruger) —, depois e-mail, depois nome completo
        (com e sem acentos). Um critério que case com mais de um usuário
        levanta ``SAPUsuarioAmbiguo``."""
        self.login()
        nome = _nome_sap(full_name)
        alvo = _sem_acentos(nome)
        codigos = [c for c in dict.fromkeys([(user_code or "").strip().upper(), derivar_user_code(full_name)]) if c]
        for codigo in codigos:
            for u in self._users_where(f"UserCode eq '{_odata(codigo)}'"):
                if _sem_acentos(_nome_sap(u.get("UserName") or "")) == alvo:
                    return u
        filtros: List[str] = []
        if email and email.strip():
            filtros.append(f"tolower(eMail) eq '{_odata(email.strip().lower())}'")
        if nome:
            filtros.append(f"UserName eq '{_odata(nome)}'")
            if alvo != nome:
                filtros.append(f"UserName eq '{_odata(alvo)}'")
        for filtro in filtros:
            itens = self._users_where(filtro)
            if len(itens) == 1:
                return itens[0]
            if len(itens) > 1:
                lista = ", ".join(f"{i.get('UserCode')} – {i.get('UserName')}" for i in itens)
                raise SAPUsuarioAmbiguo(f"Mais de um usuário SAP para [{filtro}]: {lista} — trate manualmente")
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sap_usuarios.py -q`
Expected: PASS (14 testes)

- [ ] **Step 5: Commit**

```bash
git add services/sap_api.py tests/test_sap_usuarios.py
git commit -m "fix(sap): busca de usuario interno por \$filter (Users() exige InternalKey)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 4: Bloqueio por `InternalKey` e `flow.py` passando o nome

**Files:**
- Modify: `services/sap_api.py` — `deactivate_internal_user`
- Modify: `flow.py:393-406` (`_sap_interno` do desligamento)
- Test: `tests/test_sap_usuarios.py`

**Interfaces:**
- Consumes: `find_internal_user` (Task 3), fixture `sap`.
- Produces: `SAPService.deactivate_internal_user(full_name: str, email: Optional[str] = None, dry_run: bool = False) -> dict` com `user_code, internal_key, user_name, user_locked, licenses_released`; `SAPUsuarioNaoEncontrado` quando não acha; `SAPUsuarioAmbiguo` propaga.

- [ ] **Step 1: Write the failing test**

```python
from services.sap_api import SAPUsuarioNaoEncontrado  # noqa: E402  (junto aos imports)
from flow import STEP_M365_DESLIG, STEP_SAP_DESLIG_INTERNO, STEP_UBD_DESLIG, AutomationFlowOrchestrator  # noqa: E402
from models import UserOffboardData, UserProfileType  # noqa: E402


def test_bloqueia_paola_por_internal_key(sap):
    fake, svc = sap([PAOLA])
    res = svc.deactivate_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br")
    assert res["user_code"] == "PAOLAK" and res["internal_key"] == 57 and res["user_locked"] is True
    assert fake.patches == [(57, {"Locked": "tYES"})]


def test_bloqueio_sem_usuario_levanta_nao_encontrado(sap):
    fake, svc = sap([])
    with pytest.raises(SAPUsuarioNaoEncontrado, match="PAOLAK"):
        svc.deactivate_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br")
    assert fake.patches == []


def test_flow_desligamento_interno_passa_nome_e_email_ao_sap(monkeypatch):
    monkeypatch.setattr(settings, "DRY_RUN", False, raising=False)
    monkeypatch.setattr(settings, "validate_sap", lambda: True)
    chamadas = []

    class SapStub:
        def deactivate_internal_user(self, full_name, email=None, dry_run=False):
            chamadas.append((full_name, email, dry_run))
            return {"user_code": "PAOLAK", "user_locked": True}

        def logout(self):
            return True

    orq = AutomationFlowOrchestrator(dry_run=False, interactive=False, skip_steps=[STEP_M365_DESLIG, STEP_UBD_DESLIG])
    orq.sap = SapStub()
    res = orq.run_user_offboard_flow(UserOffboardData(
        email="paola.kemel@bondmann.com.br", full_name="Paola Kemel", profile_type=UserProfileType.INTERNO,
    ))
    assert chamadas == [("Paola Kemel", "paola.kemel@bondmann.com.br", False)]
    etapa = next(r for r in res if r.step_name == STEP_SAP_DESLIG_INTERNO)
    assert etapa.status.value == "SUCCESS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sap_usuarios.py -q -k "bloque or flow"`
Expected: FAIL — `deactivate_internal_user()` recebe e-mail como 1º argumento e chama `find_internal_user_code` inexistente (`AttributeError`); o teste do flow falha porque `flow.py` chama `deactivate_internal_user(data.email, dry_run=False)`.

- [ ] **Step 3: Write minimal implementation**

Substituir `deactivate_internal_user` em `services/sap_api.py`:

```python
    def deactivate_internal_user(
        self, full_name: str, email: Optional[str] = None, dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Bloqueia o usuário interno no SAP (Locked = 'tYES') e devolve suas licenças ao estoque.

        A busca é ``find_internal_user`` (código PRIMEIRONOME+inicial, e-mail,
        nome); a escrita é ``PATCH Users(<InternalKey>)``. Levanta
        ``SAPUsuarioNaoEncontrado`` quando não acha (o chamador decide se isso é
        erro), ``SAPUsuarioAmbiguo`` quando o critério é ambíguo, e
        ``RuntimeError`` com o código/texto da Service Layer em qualquer outra
        falha — nunca devolve ``False`` silencioso.
        """
        if dry_run:
            u_code = derivar_user_code(full_name)
            logger.info(f"[DRY RUN] Inativação simulada de usuário SAP '{u_code}'.")
            return {"user_code": u_code, "user_locked": True, "status": "Simulado (Dry Run)"}

        usuario = self.find_internal_user(full_name, email)
        if not usuario:
            raise SAPUsuarioNaoEncontrado(
                f"Usuário SAP de '{full_name}' não encontrado (código {derivar_user_code(full_name)}, "
                f"e-mail {email or '—'}, nome {_nome_sap(full_name)})"
            )
        chave = int(usuario["InternalKey"])
        u_code = usuario.get("UserCode") or ""

        released = self.release_user_licenses(u_code)

        resp = self.session.patch(f"{self.base_url}/Users({chave})", json={"Locked": "tYES"}, timeout=10)
        if resp.status_code in (200, 204):
            logger.info(f"Usuário SAP '{u_code}' (InternalKey={chave}) bloqueado com sucesso (Locked=tYES).")
            return {"user_code": u_code, "internal_key": chave, "user_name": usuario.get("UserName"),
                    "user_locked": True, "licenses_released": released}
        raise RuntimeError(f"SAP recusou o bloqueio de '{u_code}' (InternalKey={chave}): {resp.status_code} - {resp.text[:300]}")
```

Em `flow.py`, dentro de `_sap_interno` do desligamento, trocar a linha
`return self.sap.deactivate_internal_user(data.email, dry_run=False)` por:

```python
                    return self.sap.deactivate_internal_user(
                        full_name=data.full_name or data.email, email=data.email, dry_run=False,
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sap_usuarios.py tests/test_worker.py -q`
Expected: PASS (17 + 22)

- [ ] **Step 5: Commit**

```bash
git add services/sap_api.py flow.py tests/test_sap_usuarios.py
git commit -m "fix(sap): bloqueio de usuario interno via Users(InternalKey), busca por nome

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 5: Criação — existência, colisão de código e factory

**Files:**
- Modify: `services/sap_api.py` — `create_internal_user` (linhas ~586–700) + novo `_codigo_livre`
- Modify: `models/factories.py:152` (`sap_user_code`)
- Test: `tests/test_sap_usuarios.py`

**Interfaces:**
- Consumes: `find_internal_user`, `_users_where`, `candidatos_user_code`, `_nome_sap`, `_odata`.
- Produces: `create_internal_user(...)` (mesma assinatura) sempre devolve `user_code` e, fora do dry-run, `internal_key`; `action` ∈ {`"criado"`, `"atualizado"`}. `SAPService._codigo_livre(full_name, user_code=None) -> str`.

- [ ] **Step 1: Write the failing test**

```python
from models import creation_data_from_payload  # noqa: E402  (junto aos imports)


def test_criacao_com_codigo_livre_faz_post_paolak(sap):
    fake, svc = sap([])
    res = svc.create_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br", password="Abc12345", licenses=[])
    assert res["action"] == "criado" and res["user_code"] == "PAOLAK" and res["internal_key"] == 1
    assert fake.posts[0]["UserCode"] == "PAOLAK" and fake.posts[0]["UserName"] == "PAOLA KEMEL"


def test_criacao_com_codigo_de_outra_pessoa_usa_proxima_letra(sap):
    fake, svc = sap([{**PAOLA, "UserName": "PAOLA KRUGER"}])
    res = svc.create_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br", password="Abc12345", licenses=[])
    assert res["user_code"] == "PAOLAKE" and fake.posts[0]["UserCode"] == "PAOLAKE"


def test_criacao_de_quem_ja_existe_reaproveita_e_desbloqueia(sap):
    fake, svc = sap([{**PAOLA, "Locked": "tYES"}])
    res = svc.create_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br", password="Abc12345", licenses=[])
    assert res["action"] == "atualizado" and res["internal_key"] == 57
    assert fake.posts == []
    assert fake.patches[0][0] == 57 and fake.patches[0][1]["Locked"] == "tNO"


def test_criacao_dry_run_usa_o_codigo_padrao(sap):
    fake, svc = sap([])
    res = svc.create_internal_user("Paola Kemel", "paola.kemel@bondmann.com.br", dry_run=True)
    assert res["user_code"] == "PAOLAK" and fake.gets == [] and fake.posts == []


def test_factory_gera_codigo_sap_no_padrao():
    dados = creation_data_from_payload({"nome_completo": "Paola Kemel", "perfil": "INTERNO",
                                        "email": "paola.kemel@bondmann.com.br"})
    assert dados.sap_user_code == "PAOLAK"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_sap_usuarios.py -q -k "criacao or factory"`
Expected: FAIL — código gerado é `paola` e a checagem de existência usa `Users('paola')` (400 engolido ⇒ POST mesmo quando existe).

- [ ] **Step 3: Write minimal implementation**

Em `create_internal_user`, trocar o trecho do início até o fim do método por:

```python
        u_name = _nome_sap(full_name)
        u_email = email.strip()
        u_superuser = "tYES" if superuser else "tNO"

        # Senha no SAP B1: política exige entre 4 e 10 caracteres
        raw_pwd = (password or "Bond2026").strip()
        if len(raw_pwd) > 10:
            clean_pwd = raw_pwd[:10]
        elif len(raw_pwd) < 4:
            clean_pwd = raw_pwd.ljust(4, "0")
        else:
            clean_pwd = raw_pwd

        chosen_licenses = [l.strip().upper() for l in (licenses or []) if l and l.strip().upper() != "NENHUMA"]

        # Valida disponibilidade de licenças
        ok_lic, lic_msg = self.validate_license_availability(chosen_licenses)
        if not ok_lic:
            raise ValueError(lic_msg)

        if dry_run:
            u_code = candidatos_user_code(full_name, user_code)[0]
            logger.info(f"[DRY RUN] Criação simulada de usuário SAP: Code={u_code}, Name={u_name}, Licenças={chosen_licenses}")
            return {
                "success": True,
                "status": "Simulado (Dry Run)",
                "user_code": u_code,
                "user_name": u_name,
                "email": u_email,
                "superuser": u_superuser,
                "licenses": chosen_licenses,
                "department": "Geral (-2)",
            }

        self.login()

        # 1. Já existe? (mesma pessoa: código com o mesmo nome, e-mail ou nome)
        existente = self.find_internal_user(full_name, email, user_code)
        if existente:
            chave = int(existente["InternalKey"])
            u_code = existente.get("UserCode") or ""
            resp_patch = self.session.patch(
                f"{self.base_url}/Users({chave})",
                json={"UserName": u_name, "eMail": u_email, "Superuser": u_superuser, "Locked": "tNO"},
                timeout=10,
            )
            if resp_patch.status_code in (200, 204):
                self.reserve_user_licenses(u_code, chosen_licenses)
                logger.info(f"Usuário SAP '{u_code}' (InternalKey={chave}) já existia e foi atualizado com sucesso.")
                return {
                    "success": True,
                    "user_code": u_code,
                    "internal_key": chave,
                    "user_name": u_name,
                    "email": u_email,
                    "superuser": u_superuser,
                    "licenses": chosen_licenses,
                    "action": "atualizado",
                }
            err = f"Falha ao atualizar usuário existente '{u_code}' no SAP: {resp_patch.status_code} - {resp_patch.text}"
            logger.error(err)
            raise RuntimeError(err)

        # 2. Cria novo usuário via POST /Users com o primeiro código livre
        u_code = self._codigo_livre(full_name, user_code)
        payload_create = {
            "UserCode": u_code,
            "UserName": u_name,
            "eMail": u_email,
            "Superuser": u_superuser,
            "UserPassword": clean_pwd,
            "Department": -2,  # Geral / Padrão
            "Locked": "tNO",
        }
        resp_create = self.session.post(f"{self.base_url}/Users", json=payload_create, timeout=10)
        if resp_create.status_code in (200, 201):
            internal_key = resp_create.json().get("InternalKey")
            self.reserve_user_licenses(u_code, chosen_licenses)
            logger.info(f"Usuário SAP '{u_code}' ({u_name}) criado com sucesso! InternalKey={internal_key}, Licenças={chosen_licenses}")
            return {
                "success": True,
                "user_code": u_code,
                "user_name": u_name,
                "email": u_email,
                "superuser": u_superuser,
                "internal_key": internal_key,
                "licenses": chosen_licenses,
                "action": "criado",
            }
        err = f"Erro ao criar usuário '{u_code}' no SAP Service Layer: {resp_create.status_code} - {resp_create.text}"
        logger.error(err)
        raise RuntimeError(err)

    def _codigo_livre(self, full_name: str, user_code: Optional[str] = None) -> str:
        """Primeiro candidato (PAOLAK, PAOLAKE, …) sem usuário no SAP."""
        for candidato in candidatos_user_code(full_name, user_code):
            if not self._users_where(f"UserCode eq '{_odata(candidato)}'"):
                return candidato
        raise RuntimeError(f"Todos os códigos SAP derivados de '{full_name}' já estão em uso — defina o código manualmente")
```

Em `models/factories.py`, trocar `sap_user_code=(nome.split()[0].lower() if interno else None),` por:

```python
        sap_user_code=(derivar_user_code(nome) if interno else None),
```

e, como primeira linha do corpo de `creation_data_from_payload`, o import local (mesmo padrão de `_senha_m365` com `MSGraphService` — evita ciclo `models` ↔ `services`, porque `services/__init__.py` carrega todos os serviços):

```python
    from services.sap_api import derivar_user_code
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_sap_usuarios.py tests/test_worker.py -q`
Expected: PASS (22 + 22)

- [ ] **Step 5: Commit**

```bash
git add services/sap_api.py models/factories.py tests/test_sap_usuarios.py
git commit -m "fix(sap): criacao de usuario interno reaproveita existente e trata colisao de codigo

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 6: Docs do worker, deploy e homologação da F1 (caso Paola)

**Files:**
- Modify (worker): `PLANO_MESTRE_AUTOMACAO.md` (cabeçalho + linha no Histórico, v1.2.0)
- Modify (portal): `plano_md_mestre_automacao_acessos_v2.md` (Matriz: itens F1)

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces: worker em produção com a correção; etapa SAP da Paola concluída.

- [ ] **Step 1: Histórico do worker**

Em `PLANO_MESTRE_AUTOMACAO.md`, atualizar **Última Atualização** e acrescentar ao fim da tabela do Histórico:

```markdown
| 2026-MM-DD | v1.2.0 | Correção do usuário interno no SAP (plano v2 do portal, F1): `Users('<código>')` dava 400/201 porque a chave da entidade é `InternalKey`; busca passa a ser `$filter` (código `PRIMEIRONOME+inicial` validado pelo nome → e-mail → nome com/sem acento; ambíguo = FAILED), bloqueio e atualização por `Users(<InternalKey>)`, criação reaproveita o existente e trata colisão (`PAOLAK` → `PAOLAKE`), factory gera o código no padrão. | Claude Code | `python -m pytest tests/test_sap_usuarios.py tests/test_worker.py` |
```

- [ ] **Step 2: Suíte e push da branch**

Run: `python -m pytest tests/test_sap_usuarios.py tests/test_worker.py -q`
Expected: tudo PASS.

```bash
git add PLANO_MESTRE_AUTOMACAO.md
git commit -m "docs: historico v1.2.0 (correcao usuario interno SAP)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin fix/sap-usuario-interno
```

Abrir PR para `master` (`gh pr create --base master`) com o resumo da causa raiz. **Merge e deploy só com o "ok" do gestor** (o Railway publica o serviço `automacao-worker` a partir do `master`).

- [ ] **Step 3: Homologação (com autorização do gestor em cada passo real)**

1. No chamado do desligamento da Paola (card do atendimento), **Simular (dry-run)** ⇒ etapa SAP ✅ com `user_code: PAOLAK`.
2. **Reexecutar pendências** (real) ⇒ só a etapa SAP roda (as outras vêm `SKIPPED — já concluída`) ⇒ ✅ `internal_key: 57` (ou o valor real da F0 #4), chamado vai para RESOLVIDO se não houver outra pendência.
3. Conferir no SAP (tela Usuários – Definição) que `PAOLAK` está com **Bloqueado** marcado.

- [ ] **Step 4: Matriz do plano v2 (repo do portal)**

Itens F1 → 🟢 Pronto (ou 🟡 Em processo se a homologação ainda não ocorreu), linha F1 idem, data do dia. Commit no portal:

```bash
git add plano_md_mestre_automacao_acessos_v2.md
git commit -m "docs(automacao): F1 (SAP usuario interno) homologada

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## F2 — Urgência e 17h (portal)

Todas as tasks de F2 rodam neste repo, numa branch nova a partir da branch do plano (para os docs irem juntos):

```bash
git checkout docs/automacao-acessos-v2 && git checkout -b feat/automacao-v2-urgencia-17h
```

Antes do primeiro pytest: `npm run build:css` (o `app.css` é gerado e gitignored).

### Task 7: Tipo de campo `checkbox` no motor de formulários

**Files:**
- Modify: `app/domain/campos_dinamicos.py:27-29` (constantes) e `validar_campos` (linhas ~96–110)
- Modify: `app/templates/portal/_campos_dinamicos.html` (novo ramo `elif`)
- Test: `tests/test_formularios_dinamicos.py`

**Interfaces:**
- Produces: `campos_dinamicos.VALOR_CHECKBOX_MARCADO = "Sim"`; tipo `"checkbox"` em `TIPOS_VALIDOS`; campo marcado grava `"Sim"` em `dados_formulario`, desmarcado não grava chave; obrigatório desmarcado ⇒ erro `Marque o campo "<label>".`

- [ ] **Step 1: Write the failing test**

Acrescentar em `tests/test_formularios_dinamicos.py` (import `VALOR_CHECKBOX_MARCADO` no bloco `from app.domain.campos_dinamicos import (...)`; import de `templates` e `Layout` no topo):

```python
from app.domain.formularios_dinamicos import Layout
from app.templating import templates

_CHECK = (CampoDef("urgente", "Urgência", "checkbox"),)


def test_checkbox_marcado_grava_sim_e_desmarcado_nao_grava():
    ok, erro, limpo = validar_campos(_CHECK, {"urgente": ["Sim"]})
    assert ok, erro
    assert limpo == {"urgente": VALOR_CHECKBOX_MARCADO}
    ok, erro, limpo = validar_campos(_CHECK, {})
    assert ok and limpo == {}


def test_checkbox_obrigatorio_desmarcado_e_recusado():
    ok, erro, _ = validar_campos((CampoDef("aceite", "Aceite", "checkbox", obrigatorio=True),), {})
    assert not ok and 'Marque o campo "Aceite"' in erro


def test_partial_renderiza_checkbox_marcado_e_desmarcado():
    tpl = templates.env.get_template("portal/_campos_dinamicos.html")
    layout = Layout(origem="acessos", chave="teste", campos=_CHECK)
    html = tpl.render(layout=layout, dados_form={"urgente": "Sim"})
    assert 'type="checkbox" name="campo__urgente" value="Sim"' in html and "checked" in html
    html = tpl.render(layout=layout, dados_form={})
    assert 'name="campo__urgente"' in html and "checked" not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_formularios_dinamicos.py -q -k checkbox`
Expected: FAIL — `ImportError: cannot import name 'VALOR_CHECKBOX_MARCADO'`

- [ ] **Step 3: Write minimal implementation**

Em `app/domain/campos_dinamicos.py`:

```python
# Tipos de campo suportados pelo partial `_campos_dinamicos.html` e pela validação.
# ``checkbox_multi``: 0..N opções marcadas — valor gravado é ``list[str]``.
# ``checkbox``: caixa única (sim/não) — marcada grava ``VALOR_CHECKBOX_MARCADO``,
# desmarcada não grava a chave (ex.: "Urgência" do desligamento, plano v2 F2).
TIPOS_VALIDOS = {"text", "textarea", "select", "date", "number", "email", "tel", "checkbox_multi", "checkbox"}
VALOR_CHECKBOX_MARCADO = "Sim"
```

Em `validar_campos`, logo depois do bloco `if campo.tipo == "checkbox_multi": ... continue`:

```python
        if campo.tipo == "checkbox":
            if any(v.strip() for v in brutos):
                limpo[campo.name] = VALOR_CHECKBOX_MARCADO
            elif campo.obrigatorio:
                return False, f'Marque o campo "{campo.label}".', {}
            continue
```

Em `_campos_dinamicos.html`, entre o fim do ramo `checkbox_multi` (`</fieldset>`) e o `{% else %}`:

```jinja
  {% elif campo.tipo == 'checkbox' %}
  {% set marcado = dados_form.get(campo.name) if dados_form else '' %}
  <label class="flex items-start gap-2 text-sm text-ink">
    <input type="checkbox" name="campo__{{ campo.name }}" value="Sim" {% if marcado %}checked{% endif %}
      class="mt-0.5 rounded ring-1 ring-line text-navy-700 focus:ring-navy-500">
    <span>
      <span class="block text-[13px] font-semibold text-navy">{{ campo.label }}{% if campo.obrigatorio %} <span class="text-pr_urgente">*</span>{% endif %}</span>
      {% if campo.ajuda %}<span class="block text-xs text-muted mt-0.5">{{ campo.ajuda }}</span>{% endif %}
    </span>
  </label>
```

e atualizar o comentário do topo do partial: "string para a maioria dos tipos, lista para `checkbox_multi`, `"Sim"`/ausente para `checkbox`".

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_formularios_dinamicos.py tests/test_formularios_quimico.py -q`
Expected: PASS (Químico sem regressão)

- [ ] **Step 5: Commit**

```bash
git add app/domain/campos_dinamicos.py app/templates/portal/_campos_dinamicos.html tests/test_formularios_dinamicos.py
git commit -m "feat(formularios): tipo de campo checkbox simples

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 8: Campo "Urgência", payload e agendamento 17h/imediato

**Files:**
- Modify: `app/domain/formularios_acessos.py` (`CAMPOS_DESLIGAMENTO`, antes de `observacoes`)
- Modify: `app/domain/automacao.py` — docstring (linha 12), `montar_payload` (ramo DESLIGAMENTO ~153–166), `calcular_executar_apos` (~206–237), `resumo_payload` (~497)
- Modify: `app/config.py:180`, `.env.example:86`
- Test: `tests/test_formularios_dinamicos.py`, `tests/test_automacao.py`

**Interfaces:**
- Consumes: `VALOR_CHECKBOX_MARCADO` (Task 7).
- Produces: `payload["urgente"]: bool` em todo job DESLIGAMENTO; `calcular_executar_apos(..., hora_desligamento: int = 17)` devolve `agora` quando `payload.get("urgente") is True`; resumo com a linha `("Urgência", "Sim — executa ao aprovar")`.

- [ ] **Step 1: Write the failing test**

Em `tests/test_formularios_dinamicos.py`:

```python
def test_desligamento_urgencia_e_opcional_e_grava_sim():
    ok, erro, limpo = validar_campos(ac.CAMPOS_DESLIGAMENTO, _desligamento(urgente=["Sim"]))
    assert ok, erro
    assert limpo["urgente"] == "Sim"
    ok, erro, limpo = validar_campos(ac.CAMPOS_DESLIGAMENTO, _desligamento())
    assert ok, erro
    assert "urgente" not in limpo
```

Em `tests/test_automacao.py`, **renomear e ajustar** o teste existente e acrescentar os novos:

```python
def test_executar_apos_desligamento_17h_e_licenca_15_dias():
    agora = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, DADOS_DESLIG, CHAMADO)
    assert dom.calcular_executar_apos(dom.TIPO_DESLIGAMENTO, p, agora=agora, feriados=set()) == datetime(
        2026, 9, 30, 20, 0, tzinfo=UTC
    )
    lic = dom.montar_payload_revogar_licenca(
        {"email": "x@bondmann.com.br", "ms_user_id": "guid", "offboard_date": "2026-09-30"}, CHAMADO
    )
    assert lic["tipo"] == "REVOGAR_LICENCA" and lic["ms_user_id"] == "guid"
    assert dom.calcular_executar_apos(dom.TIPO_REVOGAR_LICENCA, lic, agora=agora, feriados=set()) == datetime(
        2026, 10, 15, 10, 0, tzinfo=UTC
    )


def test_payload_desligamento_urgente_vira_booleano():
    assert dom.montar_payload(dom.TIPO_DESLIGAMENTO, DADOS_DESLIG, CHAMADO)["urgente"] is False
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, {**DADOS_DESLIG, "urgente": "Sim"}, CHAMADO)
    assert p["urgente"] is True


def test_executar_apos_desligamento_urgente_ignora_a_data():
    agora = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, {**DADOS_DESLIG, "urgente": "Sim"}, CHAMADO)
    assert dom.calcular_executar_apos(dom.TIPO_DESLIGAMENTO, p, agora=agora, feriados=set()) == agora


def test_resumo_mostra_urgencia_so_quando_marcada():
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, {**DADOS_DESLIG, "urgente": "Sim"}, CHAMADO)
    assert ("Urgência", "Sim — executa ao aprovar") in dom.resumo_payload(p)
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, DADOS_DESLIG, CHAMADO)
    assert all(r != "Urgência" for r, _ in dom.resumo_payload(p))


def test_padrao_de_horario_do_desligamento_e_17h():
    from app.config import Settings
    assert Settings.model_fields["automacao_hora_desligamento"].default == 17
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_automacao.py tests/test_formularios_dinamicos.py -q -k "urgen or 17h"`
Expected: FAIL — campo `urgente` desconhecido (não gravado), `KeyError: 'urgente'` no payload, horário 21:00 UTC em vez de 20:00.

- [ ] **Step 3: Write minimal implementation**

`app/domain/formularios_acessos.py`, em `CAMPOS_DESLIGAMENTO`, antes do `CampoDef("observacoes", ...)`:

```python
    CampoDef(
        "urgente", "Urgência — executar assim que a TI aprovar", "checkbox",
        ajuda="Ignora a data e o horário do desligamento: os acessos são bloqueados "
        "no momento em que a TI aprovar a automação.",
    ),
```

`app/domain/automacao.py`:
- import no topo: `from app.domain.campos_dinamicos import VALOR_CHECKBOX_MARCADO`
- docstring do módulo: trocar a linha do desligamento por
  `- **Desligamento** roda às 17h (Brasília) da data informada (gestor, 2026-09-25); data passada ou **urgência** marcada pelo RH = imediato (no clique da TI).`
- `montar_payload`, ramo DESLIGAMENTO, depois de `"encaminhar_para": ...`:

```python
                "urgente": str(dados.get("urgente") or "") == VALOR_CHECKBOX_MARCADO,
```

- `calcular_executar_apos`: assinatura `hora_desligamento: int = 17,` e o ramo:

```python
    elif tipo == TIPO_DESLIGAMENTO:
        dia = _para_date(payload.get("data_desligamento"))
        if dia and payload.get("urgente") is not True:
            alvo = datetime.combine(dia, time(hour=hora_desligamento), tzinfo=TZ_BR)
```

- `resumo_payload`, depois de `_add("Encaminhar e-mails para", ...)`:

```python
    _add("Urgência", "Sim — executa ao aprovar" if payload.get("urgente") else None)
```

`app/config.py:180`: `automacao_hora_desligamento: int = Field(default=17)`
`.env.example:86`: `AUTOMACAO_HORA_DESLIGAMENTO=17`

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_automacao.py tests/test_formularios_dinamicos.py tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/domain/formularios_acessos.py app/domain/automacao.py app/config.py .env.example tests/test_automacao.py tests/test_formularios_dinamicos.py
git commit -m "feat(automacao): urgencia no desligamento e horario padrao 17h

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 9: Selo "Urgente" e botão no card do atendimento

**Files:**
- Modify: `app/services/automacao.py:75-134` (`CardAutomacao`, `montar_card`)
- Modify: `app/templates/workspace/atendimento.html:184` (título do card) e `:253-256` (botão real)
- Test: `tests/test_automacao.py`

**Interfaces:**
- Consumes: `payload["urgente"]` (Task 8).
- Produces: `CardAutomacao.urgente: bool = False` (último campo, com default).

- [ ] **Step 1: Write the failing test**

```python
def test_card_desligamento_urgente_mostra_selo_e_botao(settings_automacao):
    repo = _RepoAcessos(subcategoria=ac.SUB_DESLIGAMENTO, dados={**DADOS_DESLIG, "urgente": "Sim"},
                        status="EM_ATENDIMENTO", operador_id=OP)
    with ws(repo, FakeAutomacaoRepo()) as c:
        html = c.get("/workspace/chamados/c1").text
    assert "Urgente — roda ao aprovar" in html
    assert "Executar agora (urgente)" in html
    assert "Sim — executa ao aprovar" in html


def test_card_desligamento_sem_urgencia_nao_mostra_selo(settings_automacao):
    repo = _RepoAcessos(subcategoria=ac.SUB_DESLIGAMENTO, dados=DADOS_DESLIG, status="EM_ATENDIMENTO", operador_id=OP)
    with ws(repo, FakeAutomacaoRepo()) as c:
        html = c.get("/workspace/chamados/c1").text
    assert "Urgente — roda ao aprovar" not in html and "Executar automação" in html


def test_executar_desligamento_urgente_roda_ja(settings_automacao):
    repo = _RepoAcessos(subcategoria=ac.SUB_DESLIGAMENTO, dados={**DADOS_DESLIG, "urgente": "Sim"},
                        status="EM_ATENDIMENTO", operador_id=OP)
    arepo = FakeAutomacaoRepo()
    with ws(repo, arepo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/automacao/executar", data={"modo": "real"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    job = arepo.criados[0]
    assert job["tipo"] == "DESLIGAMENTO" and job["dry_run"] is False and job["payload"]["urgente"] is True
    assert job["executar_apos"] <= datetime.now(UTC) + timedelta(seconds=5)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_automacao.py -q -k "card_desligamento or desligamento_urgente_roda"`
Expected: FAIL nos dois de card (selo/botão ausentes); `test_executar_desligamento_urgente_roda_ja` já deve PASSAR pela Task 8 (é regressão do fluxo completo).

- [ ] **Step 3: Write minimal implementation**

`app/services/automacao.py` — em `CardAutomacao`, depois de `aviso: str`:

```python
    urgente: bool = False  # desligamento marcado como urgente pelo RH (roda ao aprovar)
```

Em `montar_card`, trocar o cálculo do resumo por:

```python
    payload_card = ultimo["payload"] if ultimo else dom.montar_payload(tipo, dados, chamado)
    resumo = dom.resumo_payload(payload_card)
```

e passar `urgente=bool(payload_card.get("urgente")),` no `return CardAutomacao(...)`.

`app/templates/workspace/atendimento.html` — logo depois do `<h3 ...>Automação de acessos — {{ automacao.tipo_label }}</h3>`:

```jinja
          {% if automacao.urgente %}
          <span class="text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-rose-50 text-rose-800 border-rose-200">Urgente — roda ao aprovar</span>
          {% endif %}
```

e o texto do botão `modo=real`:

```jinja
            {% if automacao.pode_reexecutar %}Executar do zero{% elif automacao.urgente %}Executar agora (urgente){% else %}Executar automação{% endif %}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_automacao.py tests/test_workspace.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/automacao.py app/templates/workspace/atendimento.html tests/test_automacao.py
git commit -m "feat(automacao): selo de urgencia e botao 'executar agora' no card

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task 10: Docs da F2, suíte completa, PR e homologação

**Files:**
- Modify: `docs/automacao_api.md:141` e tabela `DESLIGAMENTO`
- Modify: `plano_md_mestre_automacao_acessos.md` (§4.3 tabela de campos; §5.2 "Agendamento")
- Modify: `docs/CHANGELOG.md` (linha no topo da tabela)
- Modify: `plano_md_mestre_automacao_acessos_v2.md` (Matriz: itens F2)

**Interfaces:**
- Consumes: Tasks 7–9.
- Produces: PR para `claude/develop`.

- [ ] **Step 1: Contrato e planos**

`docs/automacao_api.md`, tabela `DESLIGAMENTO`: trocar a linha de `data_desligamento` e acrescentar `urgente`:

```markdown
| `data_desligamento` | `YYYY-MM-DD` | informativo (o portal agendou às 17h desse dia, ou imediato se `urgente`) |
| `urgente` | bool | aditivo (sem bump de versão): o RH marcou "Urgência"; o portal já liberou o job no clique da TI — o worker não precisa fazer nada |
```

`plano_md_mestre_automacao_acessos.md`: na tabela da §4.3 acrescentar
`| \`urgente\` | checkbox | não | — | "Sim" ⇒ executa no clique da TI, ignorando a data (gestor, 2026-09-25) |`;
na §5.2, trocar "desligamento às 18h da `data_desligamento`" por "desligamento às **17h** da `data_desligamento` (gestor, 2026-09-25; antes 18h); com **urgência**, imediato".

`docs/CHANGELOG.md`, nova primeira linha da tabela:

```markdown
| 2026-MM-DD | Automação de acessos v2 — F2 · `campos_dinamicos.py`, `_campos_dinamicos.html`, `formularios_acessos.py`, `automacao.py` (domínio e serviço), `atendimento.html`, `config.py`, `docs/automacao_api.md` | **Urgência no desligamento e 17h** ([plano v2](../plano_md_mestre_automacao_acessos_v2.md), Seção 3). Motor de formulários ganha o tipo `checkbox` (marcado grava "Sim"). Desligamento ganha "Urgência — executar assim que a TI aprovar": payload `urgente: bool` (aditivo no contrato v1) e `calcular_executar_apos` libera o job no clique da TI; card mostra o selo "Urgente — roda ao aprovar" e o botão "Executar agora (urgente)". Horário padrão do desligamento agendado passa de 18h para **17h** (`AUTOMACAO_HORA_DESLIGAMENTO`, default 17). |
```

- [ ] **Step 2: Suíte completa + lint**

Run: `npm run build:css && python -m pytest -q && ruff check app tests`
Expected: tudo verde (mesmo floor de cobertura do CI).

- [ ] **Step 3: Matriz e commit**

Em `plano_md_mestre_automacao_acessos_v2.md`: itens F2 → 🟡 Em processo (código pronto, aguardando homologação), linha F2 idem, data do dia.

```bash
git add docs/automacao_api.md plano_md_mestre_automacao_acessos.md docs/CHANGELOG.md plano_md_mestre_automacao_acessos_v2.md
git commit -m "docs(automacao): F2 (urgencia e 17h) no contrato, planos e changelog

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feat/automacao-v2-urgencia-17h
gh pr create --base claude/develop --title "feat(automacao): urgencia no desligamento e horario padrao 17h (v2 F2)"
```

Corpo do PR: resumo da Seção 3 do plano v2 + lista de testes + a checagem de env do Step 4, terminando com `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 4: Env de produção (gestor)**

⚠️ O `.env.example` tinha `AUTOMACAO_HORA_DESLIGAMENTO=18`. Se essa variável estiver definida no serviço do portal no Railway, **o default 17 do código não vale** — o gestor precisa trocá-la para `17` (ou removê-la) no Railway. Pedir a confirmação antes de marcar a F2 como pronta.

- [ ] **Step 5: Homologação (após merge/deploy, com o "ok" do gestor)**

1. Abrir um chamado de desligamento de teste com data futura **e** "Urgência" marcada ⇒ card mostra o selo ⇒ **Simular (dry-run)** ⇒ o worker pega na hora.
2. O mesmo sem urgência ⇒ **Simular** roda na hora (dry-run é sempre imediato) e o **resumo** mostra a data; conferir via uma aprovação real de teste cancelada em seguida que "Execução liberada a partir de" = 17h da data.
3. Matriz do plano v2: F2 → 🟢 Pronto; commit `docs(automacao): F2 homologada`.

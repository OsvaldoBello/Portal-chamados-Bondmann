# Exibição de Região Comercial do Autor via SAP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exibir a região comercial do autor (Representante, Supervisor ou Gerente de Vendas) logo abaixo do número de contato na tela de atendimento do chamado, obtendo os dados dinamicamente do SAP Business One via Service Layer com cache em memória.

**Architecture:** Módulo de serviço dedicado (`app/services/sap_regiao.py`) consulta o SAP Business One (tabelas `BusinessPartners` e `IB_CO_REGIAO`) com timeout de 3s e resiliência fail-silent. O e-mail do autor do chamado é obtido de `auth.users` e o resultado da região é armazenado em cache por processo (`app.cache`) por 6 horas. As rotas de atendimento (`app/routes/workspace.py`) e portal (`app/routes/portal.py`) injetam `regiao_autor` no contexto Jinja para exibição direta no template (`app/templates/workspace/atendimento.html`).

**Tech Stack:** FastAPI, Jinja2, requests / urllib3 (com suporte a proxy/Tailscale), asyncpg / Supabase, pytest.

## Global Constraints

- **Fail-silent:** Falhas no SAP (timeout, credenciais inválidas, queda de rede) nunca devem quebrar a renderização da tela de atendimento nem atrasar o carregamento do chamado além de 3 segundos no pior caso (cache frio com timeout).
- **Sem migrations:** O schema do Supabase não é modificado; cache e resolução ocorrem em nível de serviço.
- **Representante:** Exibe nome/código direto da região (ex.: `057-VACARIA`).
- **Supervisor e Gerente:** Exibe contagem resumida com tooltip (ex.: `6 regiões vinculadas` e tooltip listando todas).
- **Segurança:** Nenhuma credencial do SAP no código; configuração lida de `app.config.get_settings()`.

---

### Task 1: Adicionar Configurações de SAP em `app/config.py`

**Files:**
- Modify: `app/config.py:165-175`
- Test: `tests/test_config_sap.py`

**Interfaces:**
- Consumes: Variáveis de ambiente `SAP_API_URL`, `SAP_COMPANY_DB`, `SAP_USERNAME`, `SAP_PASSWORD`, `SAP_PROXY_URL`.
- Produces: `settings.sap_api_url`, `settings.sap_company_db`, `settings.sap_username`, `settings.sap_password`, `settings.sap_proxy_url`.

- [ ] **Step 1: Escrever teste unitário para configurações do SAP**

Criar `tests/test_config_sap.py`:
```python
from app.config import Settings


def test_sap_settings_defaults(monkeypatch):
    monkeypatch.setenv("SAP_API_URL", "https://10.151.4.40:50000/b1s/v1")
    monkeypatch.setenv("SAP_COMPANY_DB", "SBO_BONDMANN_PRD")
    monkeypatch.setenv("SAP_USERNAME", "ADMTI")
    monkeypatch.setenv("SAP_PASSWORD", "calcio20")
    monkeypatch.setenv("SAP_PROXY_URL", "")

    s = Settings()
    assert s.sap_api_url == "https://10.151.4.40:50000/b1s/v1"
    assert s.sap_company_db == "SBO_BONDMANN_PRD"
    assert s.sap_username == "ADMTI"
    assert s.sap_password == "calcio20"
    assert s.sap_proxy_url == ""
```

- [ ] **Step 2: Executar teste para verificar falha**

Run: `pytest tests/test_config_sap.py -v`  
Expected: FAIL com `AttributeError` ou `ValidationError` para campos não definidos em `Settings`.

- [ ] **Step 3: Adicionar campos SAP em `app/config.py`**

Em `app/config.py`, adicionar na classe `Settings`:
```python
    # --- SAP Business One (Service Layer) ---
    sap_api_url: str = Field(default="https://10.151.4.40:50000/b1s/v1")
    sap_company_db: str = Field(default="")
    sap_username: str = Field(default="")
    sap_password: str = Field(default="")
    sap_proxy_url: str = Field(default="")
```

- [ ] **Step 4: Executar teste para verificar aprovação**

Run: `pytest tests/test_config_sap.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/config.py tests/test_config_sap.py
git commit -m "feat(config): adicionar configuracoes do SAP Service Layer"
```

---

### Task 2: Implementar Serviço `app/services/sap_regiao.py`

**Files:**
- Create: `app/services/sap_regiao.py`
- Test: `tests/test_sap_regiao.py`

**Interfaces:**
- Consumes: `app.config.get_settings`, `app.cache`, `app.db.admin_connection`.
- Produces: `RegiaoInfo`, `obter_regiao_por_email(email: str) -> RegiaoInfo | None`, `obter_regiao_do_autor(cliente_id: str) -> RegiaoInfo | None`.

- [ ] **Step 1: Escrever testes unitários completos com mocks para o serviço SAP**

Criar `tests/test_sap_regiao.py`:
```python
from unittest.mock import MagicMock, patch
import pytest
from app.services.sap_regiao import RegiaoInfo, SAPRegiaoService, obter_regiao_por_email


@pytest.fixture(autouse=True)
def limpar_cache():
    import app.cache as cache
    cache.clear()
    yield
    cache.clear()


def test_regiao_representante_unica():
    service = SAPRegiaoService()
    
    mock_session = MagicMock()
    mock_session.post.return_value.status_code = 200
    mock_session.post.return_value.json.return_value = {"SessionId": "test-session"}

    # Mock BusinessPartners
    bp_resp = MagicMock()
    bp_resp.status_code = 200
    bp_resp.json.return_value = {
        "value": [
            {"CardCode": "F000926", "CardName": "057 - DOUGLAS MELO", "CardType": "cSupplier"}
        ]
    }

    # Mock IB_CO_REGIAO
    reg_resp = MagicMock()
    reg_resp.status_code = 200
    reg_resp.json.return_value = {
        "value": [
            {"Code": "057-VACARIA", "Name": "057-VACARIA", "U_IB_CodCom1": "F000926"}
        ]
    }

    mock_session.get.side_effect = [bp_resp, reg_resp]
    service._session = mock_session

    info = service.consultar_regiao_sap("douglas.melo@bondmann.com.br")
    assert info is not None
    assert info.tipo == "representante"
    assert info.rotulo == "057-VACARIA"
    assert info.regioes == ["057-VACARIA"]
    assert info.eh_multiplo is False


def test_regiao_supervisor_multiplas():
    service = SAPRegiaoService()
    
    mock_session = MagicMock()
    mock_session.post.return_value.status_code = 200
    mock_session.post.return_value.json.return_value = {"SessionId": "test-session"}

    bp_resp = MagicMock()
    bp_resp.status_code = 200
    bp_resp.json.return_value = {
        "value": [
            {"CardCode": "RH2011", "CardName": "ELIEZER JERKE", "CardType": "cSupplier"}
        ]
    }

    reg_resp = MagicMock()
    reg_resp.status_code = 200
    reg_resp.json.return_value = {
        "value": [
            {"Code": "033-POA (ZONA NORTE)", "U_IB_CodCom3": "RH2011"},
            {"Code": "009-BENTO GONCALVES", "U_IB_CodCom3": "RH2011"},
            {"Code": "011-PANAMBI", "U_IB_CodCom3": "RH2011"},
        ]
    }

    mock_session.get.side_effect = [bp_resp, reg_resp]
    service._session = mock_session

    info = service.consultar_regiao_sap("eliezer.jerke@bondmann.com.br")
    assert info is not None
    assert info.tipo == "supervisor"
    assert info.rotulo == "3 regiões vinculadas"
    assert len(info.regioes) == 3
    assert info.eh_multiplo is True
    assert "033-POA (ZONA NORTE)" in info.detalhe


def test_regiao_cache():
    import app.cache as cache
    info_pre = RegiaoInfo(tipo="representante", rotulo="038-SANTA MARIA", regioes=["038-SANTA MARIA"], detalhe="038-SANTA MARIA", eh_multiplo=False)
    cache.set("sap_regiao:allan.toledo@bondmann.com.br", info_pre, ttl=100)

    # Calling obter_regiao_por_email shouldn't call SAP if cache hit
    with patch("app.services.sap_regiao.get_sap_regiao_service") as mock_get_svc:
        res = obter_regiao_por_email("allan.toledo@bondmann.com.br")
        assert res == info_pre
        mock_get_svc.assert_not_called()


def test_regiao_sap_offline():
    service = SAPRegiaoService()
    mock_session = MagicMock()
    mock_session.post.side_effect = Exception("Connection timed out")
    service._session = mock_session

    info = service.consultar_regiao_sap("qualquer@bondmann.com.br")
    assert info is None
```

- [ ] **Step 2: Executar teste para verificar falha**

Run: `pytest tests/test_sap_regiao.py -v`  
Expected: FAIL com `ModuleNotFoundError: No module named 'app.services.sap_regiao'`.

- [ ] **Step 3: Implementar `app/services/sap_regiao.py`**

Criar `app/services/sap_regiao.py`:
```python
"""Serviço de consulta de região comercial no SAP Business One Service Layer."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
import urllib.parse
import urllib3

import requests

import app.cache as cache
from app.config import get_settings
from app.db import admin_connection

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("app.services.sap_regiao")

CACHE_TTL_REGIAO_S = 21600.0  # 6 horas
CACHE_TTL_VAZIO_S = 3600.0    # 1 hora para e-mails sem cadastro no SAP
CACHE_TTL_EMAIL_S = 86400.0   # 24 horas para id -> e-mail


@dataclass(frozen=True)
class RegiaoInfo:
    tipo: str  # "representante" | "supervisor" | "gerente"
    rotulo: str  # ex: "057-VACARIA" ou "6 regiões vinculadas"
    regioes: list[str]  # lista de códigos das regiões
    detalhe: str  # ex: "033-POA, 009-BENTO..."
    eh_multiplo: bool  # True se len(regioes) > 1


class SAPRegiaoService:
    def __init__(self) -> None:
        settings = get_settings()
        default_url = "https://10.151.4.40:50000/b1s/v1"
        self.base_url = (settings.sap_api_url or default_url).rstrip("/")
        if not self.base_url.endswith("/b1s/v1"):
            self.base_url = f"{self.base_url}/b1s/v1"
        self.company_db = settings.sap_company_db
        self.username = settings.sap_username
        self.password = settings.sap_password
        self.proxy_url = settings.sap_proxy_url

        self._session: requests.Session | None = None
        self._session_id: str | None = None

    def _get_session(self) -> requests.Session:
        if self._session is not None:
            return self._session
        s = requests.Session()
        s.verify = False
        if self.proxy_url:
            s.proxies = {"http": self.proxy_url, "https": self.proxy_url}
            s.trust_env = False
        self._session = s
        return s

    def _login(self, session: requests.Session) -> bool:
        if self._session_id:
            return True
        if not (self.company_db and self.username and self.password):
            logger.debug("Credenciais SAP incompletas para login.")
            return False
        try:
            url = f"{self.base_url}/Login"
            payload = {
                "CompanyDB": self.company_db,
                "UserName": self.username,
                "Password": self.password,
            }
            resp = session.post(url, json=payload, timeout=3)
            if resp.status_code == 200:
                self._session_id = resp.json().get("SessionId")
                return True
            logger.warning("Falha de autenticação no SAP Service Layer (status=%s)", resp.status_code)
            return False
        except Exception as exc:
            logger.warning("Erro de conexão no login SAP Service Layer: %s", exc)
            return False

    def consultar_regiao_sap(self, email: str) -> RegiaoInfo | None:
        email_limpo = (email or "").strip().lower()
        if not email_limpo:
            return None

        try:
            session = self._get_session()
            if not self._login(session):
                return None

            # 1. Localiza os BusinessPartners pelo e-mail
            bp_url = f"{self.base_url}/BusinessPartners"
            filter_bp = f"EmailAddress eq '{email_limpo}'"
            bp_resp = session.get(
                bp_url,
                params={"$filter": filter_bp, "$select": "CardCode,CardName,CardType,EmailAddress,Valid"},
                timeout=3,
            )
            if bp_resp.status_code != 200:
                logger.warning("SAP BusinessPartners respondeu status %s", bp_resp.status_code)
                return None

            bps = bp_resp.json().get("value", [])
            if not bps:
                return None

            # Prioriza fornecedores (cSupplier)
            fornecedores = [b for b in bps if b.get("CardType") == "cSupplier"]
            alvos = fornecedores if fornecedores else bps
            card_codes = [b.get("CardCode") for b in alvos if b.get("CardCode")]

            for card_code in card_codes:
                # 2. Testa Representante (U_IB_CodCom1)
                r_rep = session.get(
                    f"{self.base_url}/IB_CO_REGIAO",
                    params={
                        "$filter": f"U_IB_CodCom1 eq '{card_code}'",
                        "$select": "Code,Name",
                    },
                    timeout=3,
                )
                if r_rep.status_code == 200:
                    regs = r_rep.json().get("value", [])
                    if regs:
                        nomes = [r.get("Code") or r.get("Name") for r in regs if r.get("Code") or r.get("Name")]
                        if nomes:
                            return RegiaoInfo(
                                tipo="representante",
                                rotulo=nomes[0],
                                regioes=nomes,
                                detalhe=nomes[0],
                                eh_multiplo=len(nomes) > 1,
                            )

                # 3. Testa Supervisor (U_IB_CodCom3)
                r_sup = session.get(
                    f"{self.base_url}/IB_CO_REGIAO",
                    params={
                        "$filter": f"U_IB_CodCom3 eq '{card_code}'",
                        "$select": "Code,Name",
                    },
                    timeout=3,
                )
                if r_sup.status_code == 200:
                    regs = r_sup.json().get("value", [])
                    if regs:
                        nomes = [r.get("Code") or r.get("Name") for r in regs if r.get("Code") or r.get("Name")]
                        if nomes:
                            qtd = len(nomes)
                            rotulo = nomes[0] if qtd == 1 else f"{qtd} regiões vinculadas"
                            return RegiaoInfo(
                                tipo="supervisor",
                                rotulo=rotulo,
                                regioes=nomes,
                                detalhe=", ".join(nomes),
                                eh_multiplo=qtd > 1,
                            )

                # 4. Testa Gerente (U_IB_CodCom4)
                r_ger = session.get(
                    f"{self.base_url}/IB_CO_REGIAO",
                    params={
                        "$filter": f"U_IB_CodCom4 eq '{card_code}'",
                        "$select": "Code,Name",
                    },
                    timeout=3,
                )
                if r_ger.status_code == 200:
                    regs = r_ger.json().get("value", [])
                    if regs:
                        nomes = [r.get("Code") or r.get("Name") for r in regs if r.get("Code") or r.get("Name")]
                        if nomes:
                            qtd = len(nomes)
                            rotulo = nomes[0] if qtd == 1 else f"{qtd} regiões vinculadas"
                            return RegiaoInfo(
                                tipo="gerente",
                                rotulo=rotulo,
                                regioes=nomes,
                                detalhe=", ".join(nomes),
                                eh_multiplo=qtd > 1,
                            )

            return None
        except Exception as exc:
            logger.warning("Falha ao consultar região no SAP para %s: %s", email, exc)
            return None


_instancia_service: SAPRegiaoService | None = None


def get_sap_regiao_service() -> SAPRegiaoService:
    global _instancia_service
    if _instancia_service is None:
        _instancia_service = SAPRegiaoService()
    return _instancia_service


def obter_regiao_por_email(email: str) -> RegiaoInfo | None:
    if not email:
        return None
    cache_key = f"sap_regiao:{email.strip().lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        # Pode ter cacheado sentinela (None) para e-mail sem registro
        return None if cached == "__VAZIO__" else cached

    svc = get_sap_regiao_service()
    info = svc.consultar_regiao_sap(email)
    if info is not None:
        cache.set(cache_key, info, ttl=CACHE_TTL_REGIAO_S)
    else:
        cache.set(cache_key, "__VAZIO__", ttl=CACHE_TTL_VAZIO_S)
    return info


async def obter_email_do_autor(cliente_id: str) -> str | None:
    if not cliente_id:
        return None
    cache_key = f"user_email:{cliente_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with admin_connection() as conn:
        email = await conn.fetchval(
            "SELECT email FROM auth.users WHERE id = $1::uuid", cliente_id
        )
        if email:
            cache.set(cache_key, email, ttl=CACHE_TTL_EMAIL_S)
            return email
    return None


async def obter_regiao_do_autor(cliente_id: str) -> RegiaoInfo | None:
    email = await obter_email_do_autor(cliente_id)
    if not email:
        return None
    return obter_regiao_por_email(email)
```

- [ ] **Step 4: Executar teste para verificar aprovação**

Run: `pytest tests/test_sap_regiao.py -v`  
Expected: PASS (4 tests passed)

- [ ] **Step 5: Commit**

```bash
git add app/services/sap_regiao.py tests/test_sap_regiao.py
git commit -m "feat(sap): implementar servico de consulta de regiao comercial"
```

---

### Task 3: Injetar `regiao_autor` nas Rotas de Atendimento e Portal

**Files:**
- Modify: `app/routes/workspace.py:560-605`
- Modify: `app/routes/portal.py:320-355`
- Test: `tests/test_workspace_regiao.py`

**Interfaces:**
- Consumes: `app.services.sap_regiao.obter_regiao_do_autor`.
- Produces: Contexto de template `ctx_render["regiao_autor"]`.

- [ ] **Step 1: Escrever teste de integração das rotas verificando presença de `regiao_autor` no contexto**

Criar `tests/test_workspace_regiao.py`:
```python
from unittest.mock import AsyncMock, patch
import pytest
from app.services.sap_regiao import RegiaoInfo


@pytest.mark.asyncio
async def test_obter_regiao_do_autor_integracao():
    with patch("app.services.sap_regiao.admin_connection") as mock_conn:
        mock_ctx = AsyncMock()
        mock_ctx.fetchval.return_value = "douglas.melo@bondmann.com.br"
        mock_conn.return_value.__aenter__.return_value = mock_ctx

        with patch("app.services.sap_regiao.obter_regiao_por_email") as mock_por_email:
            info = RegiaoInfo(tipo="representante", rotulo="057-VACARIA", regioes=["057-VACARIA"], detalhe="057-VACARIA", eh_multiplo=False)
            mock_por_email.return_value = info

            from app.services.sap_regiao import obter_regiao_do_autor
            res = await obter_regiao_do_autor("c513fce8-e66a-4c35-8355-deb576db0eb2")
            assert res == info
            mock_por_email.assert_called_once_with("douglas.melo@bondmann.com.br")
```

- [ ] **Step 2: Executar teste**

Run: `pytest tests/test_workspace_regiao.py -v`  
Expected: PASS

- [ ] **Step 3: Modificar `app/routes/workspace.py` para carregar e passar `regiao_autor`**

Em `app/routes/workspace.py`, importar:
```python
from app.services.sap_regiao import obter_regiao_do_autor
```
E dentro de `_carregar_atendimento` (por volta da linha 562), obter `regiao_autor`:
```python
    regiao_autor = await obter_regiao_do_autor(str(chamado.get("cliente_id") or ""))
```
E incluir no dicionário `ctx_render`:
```python
    ctx_render = {
        "perfil": ctx.perfil,
        "chamado": chamado,
        "regiao_autor": regiao_autor,
        ...
```

- [ ] **Step 4: Modificar `app/routes/portal.py` para passar `regiao_autor` no detalhe do chamado**

Em `app/routes/portal.py`, importar `from app.services.sap_regiao import obter_regiao_do_autor` e em `detalhe_chamado`, carregar `regiao_autor = await obter_regiao_do_autor(str(chamado.get("cliente_id") or ""))` e passar no contexto.

- [ ] **Step 5: Executar suite de testes para verificar ausência de quebras**

Run: `pytest tests/test_workspace.py tests/test_portal.py -q`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/routes/workspace.py app/routes/portal.py tests/test_workspace_regiao.py
git commit -m "feat(routes): injetar regiao_autor nas rotas de workspace e portal"
```

---

### Task 4: Atualizar os Templates com a Exibição da Região

**Files:**
- Modify: `app/templates/workspace/atendimento.html:65-75`
- Modify: `app/templates/portal/chamado_detalhe.html:45-50`
- Test: `tests/test_templates_regiao.py`

**Interfaces:**
- Consumes: `regiao_autor` (`rotulo`, `eh_multiplo`, `detalhe`).
- Produces: Linha formatada no HTML abaixo de `Contato: {{ chamado.telefone_contato }}`.

- [ ] **Step 1: Escrever teste de renderização do template Jinja**

Criar `tests/test_templates_regiao.py`:
```python
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader
from app.services.sap_regiao import RegiaoInfo


def test_render_atendimento_com_regiao_unica():
    from app.templating import criar_jinja_env
    env = criar_jinja_env()
    template = env.get_template("workspace/atendimento.html")

    regiao = RegiaoInfo(tipo="representante", rotulo="057-VACARIA", regioes=["057-VACARIA"], detalhe="057-VACARIA", eh_multiplo=False)
    
    html = template.render(
        chamado={
            "id": "1", "codigo": "BD-2026-00001", "titulo": "Teste", "descricao": "Desc",
            "cliente_nome": "Douglas Melo", "telefone_contato": "54996012120",
            "status": "EM_ATENDIMENTO", "prioridade": "URGENTE", "created_at": None,
            "limite_resolucao": None
        },
        regiao_autor=regiao,
        perfil={"role": "ADMIN"},
        mensagens=[],
        observadores=[],
        combinados=[],
        candidatos_combinacao=[],
        operadores=[],
        departamentos=[],
        categorias_edit=[],
        subcategorias_edit=[],
        status_validos=[],
        prioridades=[],
        origem="",
    )
    assert "Região: 057-VACARIA" in html or "057-VACARIA" in html
```

- [ ] **Step 2: Modificar `app/templates/workspace/atendimento.html`**

No bloco de informações do autor (por volta da linha 66):
```html
          <div class="text-right text-xs text-muted">
            <div>Autor: {{ chamado.cliente_nome or "—" }}</div>
            {% if chamado.telefone_contato %}
            <div>Contato: {{ chamado.telefone_contato }}</div>
            {% endif %}
            {% if regiao_autor %}
            <div class="flex items-center justify-end gap-1">
              <span>Região:</span>
              {% if regiao_autor.eh_multiplo %}
              <span class="font-medium text-navy cursor-help underline decoration-dotted" title="{{ regiao_autor.detalhe }}">
                {{ regiao_autor.rotulo }} ℹ️
              </span>
              {% else %}
              <span class="font-medium text-navy">{{ regiao_autor.rotulo }}</span>
              {% endif %}
            </div>
            {% endif %}
            <div>Aberto {{ fmt_dt(chamado.created_at) }}</div>
```

- [ ] **Step 3: Modificar `app/templates/portal/chamado_detalhe.html`**

No bloco equivalente do portal:
```html
      {% if chamado.telefone_contato %}
      <div>Contato: {{ chamado.telefone_contato }}</div>
      {% endif %}
      {% if regiao_autor %}
      <div class="flex items-center justify-end gap-1">
        <span>Região:</span>
        {% if regiao_autor.eh_multiplo %}
        <span class="font-medium text-navy cursor-help underline decoration-dotted" title="{{ regiao_autor.detalhe }}">
          {{ regiao_autor.rotulo }} ℹ️
        </span>
        {% else %}
        <span class="font-medium text-navy">{{ regiao_autor.rotulo }}</span>
        {% endif %}
      </div>
      {% endif %}
```

- [ ] **Step 4: Executar testes**

Run: `pytest tests/test_templates_regiao.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/templates/workspace/atendimento.html app/templates/portal/chamado_detalhe.html tests/test_templates_regiao.py
git commit -m "feat(ui): exibir regiao do autor abaixo do contato no chamado"
```

---

### Task 5: Validação End-to-End Real com SAP e Suíte Completa

**Files:**
- Verify: Todas as alterações implementadas.

- [ ] **Step 1: Testar consulta real no SAP com Douglas Melo (`BD-2026-01165`)**

Executar script de validação real conectando ao banco e ao SAP para o chamado do print:
```bash
python -c "import asyncio; from app.services.sap_regiao import obter_regiao_do_autor; print(asyncio.run(obter_regiao_do_autor('c513fce8-e66a-4c35-8355-deb576db0eb2')))"
```
Expected: `RegiaoInfo(tipo='representante', rotulo='057-VACARIA', regioes=['057-VACARIA'], detalhe='057-VACARIA', eh_multiplo=False)`

- [ ] **Step 2: Rodar suíte completa de testes do repositório**

Run: `pytest -q`  
Expected: Todos os testes existentes e novos passando 100%.

- [ ] **Step 3: Commit e Atualização de Docs**

Atualizar `docs/CHANGELOG.md` e commitar.

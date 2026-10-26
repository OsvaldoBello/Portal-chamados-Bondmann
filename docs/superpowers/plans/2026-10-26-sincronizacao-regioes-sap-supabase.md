# Sincronização e Cache em Banco das Regiões Comerciais SAP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o Portal de Chamados exiba a região comercial do autor (representante, supervisor e gerente) instantaneamente (<5ms) em produção, lendo de uma tabela espelho sincronizada no Supabase (`sap_regioes_autores`), sem depender de túnel Tailscale ou chamadas externas HTTP no container web.

**Architecture:** A tabela `public.sap_regioes_autores` armazena o mapeamento `email -> {tipo, rotulo, regioes, detalhe, eh_multiplo}` protegido por RLS. O serviço `app/services/sap_regiao.py` consulta o Supabase antes do cache em memória e do fallback SAP. A rota `POST /api/automacao/sap-regioes/sync` permite que o worker de automação (já conectado ao Tailscale) envie atualizações em lote. Um script de backfill (`scripts/backfill_sap_regioes.py`) faz a carga inicial completa a partir da Service Layer do SAP.

**Tech Stack:** PostgreSQL (Supabase / asyncpg), FastAPI, Jinja2, httpx, pytest, ruff.

## Global Constraints

- **Sem latência adicional no Portal:** A leitura da região na abertura do chamado deve ocorrer via consulta indexada ao PostgreSQL em <5ms.
- **Fail-silent:** Se um autor não tiver cadastro de parceiro comercial (usuário interno ou cliente comum), a tela renderiza sem atraso e sem o campo "Região:".
- **Isolamento de Segurança:** Escrita na tabela `sap_regioes_autores` permitida apenas para `ADMIN` / `service_role` ou mediante `X-Automacao-Token` na API do worker.
- **Compatibilidade Retroativa:** `app/services/sap_regiao.py` mantém a assinatura `RegiaoInfo` e `obter_regiao_do_autor(cliente_id: str)`.

---

### Task 1: Criar Migration `0098_sap_regioes_autores.sql` no Supabase

**Files:**
- Create: `supabase/migrations/0098_sap_regioes_autores.sql`
- Test: `tests/test_migration_0098.py`

**Interfaces:**
- Produces: Tabela `public.sap_regioes_autores` com RLS habilitado e políticas para `authenticated` (SELECT) e `ADMIN` (ALL).

- [ ] **Step 1: Criar arquivo de migration SQL**

Criar `supabase/migrations/0098_sap_regioes_autores.sql`:
```sql
-- Migration 0098: Tabela de cache durável de regiões comerciais SAP
-- Permite ao portal exibir a região comercial do autor diretamente pelo banco em <5ms,
-- sem depender de túnel Tailscale no container web de produção.

CREATE TABLE IF NOT EXISTS public.sap_regioes_autores (
    email TEXT PRIMARY KEY,
    card_code TEXT NOT NULL,
    card_name TEXT,
    tipo TEXT NOT NULL CHECK (tipo IN ('representante', 'supervisor', 'gerente')),
    rotulo TEXT NOT NULL,
    regioes TEXT[] NOT NULL DEFAULT '{}',
    detalhe TEXT NOT NULL DEFAULT '',
    eh_multiplo BOOLEAN NOT NULL DEFAULT false,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sap_regioes_autores_card_code
    ON public.sap_regioes_autores(card_code);

ALTER TABLE public.sap_regioes_autores ENABLE ROW LEVEL SECURITY;

-- Leitura: qualquer usuário autenticado (atendente ou solicitante)
CREATE POLICY "sap_regioes_autores_select"
    ON public.sap_regioes_autores
    FOR SELECT
    TO authenticated
    USING (true);

-- Escrita: apenas ADMIN ou tarefas administrativas internas
CREATE POLICY "sap_regioes_autores_admin_write"
    ON public.sap_regioes_autores
    FOR ALL
    TO authenticated
    USING (public.auth_role() = 'ADMIN')
    WITH CHECK (public.auth_role() = 'ADMIN');

COMMENT ON TABLE public.sap_regioes_autores IS
    'Espelho sincronizado de regiões comerciais (IB_CO_REGIAO / OCRD) do SAP Business One para exibição imediata no Portal.';
```

- [ ] **Step 2: Escrever teste de sanidade para a migration**

Criar `tests/test_migration_0098.py`:
```python
from pathlib import Path


def test_migration_0098_syntax_and_structure():
    migration_path = Path("supabase/migrations/0098_sap_regioes_autores.sql")
    assert migration_path.exists()
    content = migration_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS public.sap_regioes_autores" in content
    assert "ENABLE ROW LEVEL SECURITY" in content
    assert "sap_regioes_autores_select" in content
    assert "sap_regioes_autores_admin_write" in content
```

- [ ] **Step 3: Executar teste da migration**

Run: `.venv\Scripts\pytest tests/test_migration_0098.py -v`  
Expected: PASS

- [ ] **Step 4: Aplicar migration no banco Supabase de desenvolvimento/produção**

Executar script one-liner para aplicar DDL via `admin_connection()` no banco configurado em `.env`:
```bash
.venv\Scripts\python -c "import asyncio, dotenv; dotenv.load_dotenv(); from app.db import admin_connection; ddl = open('supabase/migrations/0098_sap_regioes_autores.sql', encoding='utf-8').read(); asyncio.run((lambda: [admin_connection().__aenter__().then(lambda c: c.execute(ddl))])())"
```
Ou via script Python seguro que aplica o DDL.

- [ ] **Step 5: Commit**

```bash
git add supabase/migrations/0098_sap_regioes_autores.sql tests/test_migration_0098.py
git commit -m "feat(db): migration 0098 adiciona tabela sap_regioes_autores"
```

---

### Task 2: Atualizar `app/services/sap_regiao.py` para Priorizar Leitura do Banco

**Files:**
- Modify: `app/services/sap_regiao.py`
- Test: `tests/test_sap_regiao.py`

**Interfaces:**
- Consumes: `app.db.admin_connection`, `app.cache`, tabela `public.sap_regioes_autores`.
- Produces: `async def obter_regiao_do_banco(email: str) -> RegiaoInfo | None`, `async def obter_regiao_do_autor(cliente_id: str) -> RegiaoInfo | None`.

- [ ] **Step 1: Escrever teste unitário para leitura da região a partir do banco**

Adicionar em `tests/test_sap_regiao.py`:
```python
from unittest.mock import AsyncMock, patch
import pytest
from app.services.sap_regiao import RegiaoInfo, obter_regiao_do_autor


@pytest.mark.asyncio
async def test_obter_regiao_do_autor_via_banco():
    cliente_id = "1d2f1b28-da53-4606-ba0b-5558f07ad725"
    mock_row_email = "helmano.silva@bondmann.com.br"
    mock_db_regiao = {
        "tipo": "supervisor",
        "rotulo": "6 regiões vinculadas",
        "regioes": ["136-VALE DO ACO", "069-DIVINOPOLIS"],
        "detalhe": "136-VALE DO ACO, 069-DIVINOPOLIS",
        "eh_multiplo": True,
    }

    with patch("app.services.sap_regiao.obter_email_do_autor", new_callable=AsyncMock) as mock_email:
        mock_email.return_value = mock_row_email
        with patch("app.services.sap_regiao.obter_regiao_do_banco", new_callable=AsyncMock) as mock_banco:
            mock_banco.return_value = RegiaoInfo(
                tipo=mock_db_regiao["tipo"],
                rotulo=mock_db_regiao["rotulo"],
                regioes=mock_db_regiao["regioes"],
                detalhe=mock_db_regiao["detalhe"],
                eh_multiplo=mock_db_regiao["eh_multiplo"],
            )
            info = await obter_regiao_do_autor(cliente_id)
            assert info is not None
            assert info.tipo == "supervisor"
            assert info.rotulo == "6 regiões vinculadas"
            assert info.eh_multiplo is True
            mock_banco.assert_awaited_once_with(mock_row_email)
```

- [ ] **Step 2: Executar teste para verificar falha**

Run: `.venv\Scripts\pytest tests/test_sap_regiao.py -k test_obter_regiao_do_autor_via_banco -v`  
Expected: FAIL (pois `obter_regiao_do_banco` ainda não existe).

- [ ] **Step 3: Implementar `obter_regiao_do_banco` e atualizar `obter_regiao_do_autor` em `app/services/sap_regiao.py`**

Adicionar em `app/services/sap_regiao.py`:
```python
async def obter_regiao_do_banco(email: str) -> RegiaoInfo | None:
    """Busca o espelho de região comercial armazenado no Supabase."""
    email_limpo = (email or "").strip().lower()
    if not email_limpo:
        return None

    try:
        async with admin_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT tipo, rotulo, regioes, detalhe, eh_multiplo
                FROM public.sap_regioes_autores
                WHERE email = $1
                """,
                email_limpo,
            )
            if row:
                return RegiaoInfo(
                    tipo=row["tipo"],
                    rotulo=row["rotulo"],
                    regioes=list(row["regioes"] or []),
                    detalhe=row["detalhe"] or "",
                    eh_multiplo=bool(row["eh_multiplo"]),
                )
    except Exception as exc:
        logger.warning("Falha ao consultar sap_regioes_autores para %s: %s", email_limpo, exc)
    return None
```

E atualizar `obter_regiao_do_autor`:
```python
async def obter_regiao_do_autor(cliente_id: str) -> RegiaoInfo | None:
    try:
        email = await obter_email_do_autor(cliente_id)
        if not email:
            return None

        cache_key = f"sap_regiao:{email.strip().lower()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return None if cached == "__VAZIO__" else cached

        # 1. Consulta o espelho no banco Supabase (<2ms)
        info = await obter_regiao_do_banco(email)
        if info is not None:
            cache.set(cache_key, info, ttl=CACHE_TTL_REGIAO_S)
            return info

        # 2. Fallback SAP direto (se banco não tiver e rede/SAP estiver disponível)
        svc = get_sap_regiao_service()
        if svc.company_db and svc.username and svc.password:
            info = svc.consultar_regiao_sap(email)
            if info is not None:
                cache.set(cache_key, info, ttl=CACHE_TTL_REGIAO_S)
                return info

        # 3. Usuário sem cadastro comercial
        cache.set(cache_key, "__VAZIO__", ttl=CACHE_TTL_VAZIO_S)
        return None
    except Exception as exc:
        logger.warning("Falha ao obter regiao do autor para cliente_id=%s: %s", cliente_id, exc)
        return None
```

- [ ] **Step 4: Executar testes para verificar aprovação**

Run: `.venv\Scripts\pytest tests/test_sap_regiao.py -v`  
Expected: PASS (todos os testes passando).

- [ ] **Step 5: Commit**

```bash
git add app/services/sap_regiao.py tests/test_sap_regiao.py
git commit -m "feat(sap): consultar sap_regioes_autores no banco com fallback SAP"
```

---

### Task 3: Criar Endpoint de Ingestão de Regiões na API do Worker

**Files:**
- Modify: `app/routes/automacao_api.py`
- Test: `tests/test_automacao_api_regioes.py`

**Interfaces:**
- Consumes: Header `X-Automacao-Token`, payload JSON com lista de regiões.
- Produces: Rota `POST /api/automacao/sap-regioes/sync`, persistindo em `public.sap_regioes_autores`.

- [ ] **Step 1: Escrever testes para o endpoint de sincronização de regiões**

Criar `tests/test_automacao_api_regioes.py`:
```python
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_sync_sap_regioes_sem_token_retorna_401(monkeypatch):
    monkeypatch.setenv("AUTOMACAO_WORKER_TOKEN", "token-secreto-123")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.post("/api/automacao/sap-regioes/sync", json=[])
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sync_sap_regioes_sucesso(monkeypatch):
    monkeypatch.setenv("AUTOMACAO_WORKER_TOKEN", "token-secreto-123")
    payload = [
        {
            "email": "helmano.silva@bondmann.com.br",
            "card_code": "RH2021",
            "card_name": "HELMANO SILVA",
            "tipo": "supervisor",
            "rotulo": "6 regiões vinculadas",
            "regioes": ["136-VALE DO ACO", "069-DIVINOPOLIS"],
            "detalhe": "136-VALE DO ACO, 069-DIVINOPOLIS",
            "eh_multiplo": True,
        }
    ]

    with patch("app.routes.automacao_api.salvar_sap_regioes_em_lote", new_callable=AsyncMock) as mock_salvar:
        mock_salvar.return_value = 1
        headers = {"X-Automacao-Token": "token-secreto-123"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/automacao/sap-regioes/sync", json=payload, headers=headers)
            assert resp.status_code == 200
            assert resp.json() == {"ok": True, "processados": 1}
            mock_salvar.assert_awaited_once()
```

- [ ] **Step 2: Executar teste para verificar falha**

Run: `.venv\Scripts\pytest tests/test_automacao_api_regioes.py -v`  
Expected: FAIL (404/Not Found).

- [ ] **Step 3: Implementar função `salvar_sap_regioes_em_lote` e rota em `app/routes/automacao_api.py`**

Adicionar função no repositório / helper:
```python
async def salvar_sap_regioes_em_lote(itens: list[dict[str, Any]]) -> int:
    if not itens:
        return 0
    from app.db import admin_connection
    import app.cache as cache

    total = 0
    async with admin_connection() as conn:
        for it in itens:
            email = (it.get("email") or "").strip().lower()
            if not email:
                continue
            await conn.execute(
                """
                INSERT INTO public.sap_regioes_autores (
                    email, card_code, card_name, tipo, rotulo, regioes, detalhe, eh_multiplo, atualizado_em
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
                ON CONFLICT (email) DO UPDATE SET
                    card_code = EXCLUDED.card_code,
                    card_name = EXCLUDED.card_name,
                    tipo = EXCLUDED.tipo,
                    rotulo = EXCLUDED.rotulo,
                    regioes = EXCLUDED.regioes,
                    detalhe = EXCLUDED.detalhe,
                    eh_multiplo = EXCLUDED.eh_multiplo,
                    atualizado_em = now()
                """,
                email,
                str(it.get("card_code") or ""),
                it.get("card_name"),
                str(it.get("tipo") or "representante"),
                str(it.get("rotulo") or ""),
                list(it.get("regioes") or []),
                str(it.get("detalhe") or ""),
                bool(it.get("eh_multiplo")),
            )
            cache.delete(f"sap_regiao:{email}")
            total += 1
    return total
```

E no router de automação:
```python
@router.post("/sap-regioes/sync")
async def sync_sap_regioes(
    request: Request,
    payload: list[dict[str, Any]],
    worker: str = Depends(validar_worker_token),
) -> dict[str, Any]:
    total = await salvar_sap_regioes_em_lote(payload)
    return {"ok": True, "processados": total}
```

- [ ] **Step 4: Executar testes da API de automação**

Run: `.venv\Scripts\pytest tests/test_automacao_api_regioes.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/routes/automacao_api.py tests/test_automacao_api_regioes.py
git commit -m "feat(automacao): endpoint de sincronizacao de sap_regioes_autores"
```

---

### Task 4: Criar Script de Carga/Backfill `scripts/backfill_sap_regioes.py`

**Files:**
- Create: `scripts/backfill_sap_regioes.py`
- Test: Execução real contra SAP e Supabase.

**Interfaces:**
- Consumes: `SAPRegiaoService` (login, `IB_CO_REGIAO`, `BusinessPartners`), `admin_connection()`.
- Produces: População de todas as regiões comerciais na tabela `public.sap_regioes_autores`.

- [ ] **Step 1: Escrever script `scripts/backfill_sap_regioes.py`**

Criar `scripts/backfill_sap_regioes.py`:
```python
"""Script de backfill das regiões comerciais do SAP Business One para o Supabase."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Adiciona raiz do projeto ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dotenv
dotenv.load_dotenv(ROOT / ".env")

from app.db import admin_connection
from app.services.sap_regiao import SAPRegiaoService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_sap_regioes")


async def executar_backfill():
    svc = SAPRegiaoService()
    session = svc._get_session()
    session.timeout = 15.0

    logger.info("Conectando ao SAP Service Layer em %s...", svc.base_url)
    if not svc._login(session):
        logger.error("Falha ao autenticar no SAP Service Layer.")
        return False

    # 1. Recupera todas as regiões da IB_CO_REGIAO (paginado)
    regioes_sap = []
    skip = 0
    while True:
        resp = session.get(
            f"{svc.base_url}/IB_CO_REGIAO",
            params={
                "$select": "Code,Name,U_IB_CodCom1,U_IB_CodCom3,U_IB_CodCom4",
                "$skip": skip,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.error("Erro ao consultar IB_CO_REGIAO: %s", resp.text)
            break
        dados = resp.json().get("value", [])
        if not dados:
            break
        regioes_sap.extend(dados)
        skip += len(dados)

    logger.info("Total de regiões encontradas em IB_CO_REGIAO: %d", len(regioes_sap))

    # Mapeamento de CardCode -> {tipo: ..., regioes: set()}
    cardcode_map: dict[str, dict] = {}
    for r in regioes_sap:
        nome_reg = r.get("Code") or r.get("Name")
        if not nome_reg:
            continue

        c1 = r.get("U_IB_CodCom1")
        c3 = r.get("U_IB_CodCom3")
        c4 = r.get("U_IB_CodCom4")

        for code, tipo in [(c1, "representante"), (c3, "supervisor"), (c4, "gerente")]:
            if code and code != "RH2020":
                if code not in cardcode_map:
                    cardcode_map[code] = {"tipo": tipo, "regioes": set()}
                cardcode_map[code]["regioes"].add(nome_reg)

    logger.info("Total de CardCodes ativos encontrados: %d", len(cardcode_map))

    # 2. Busca e-mail e nome de cada CardCode em BusinessPartners
    registros_finais = []
    for card_code, info in cardcode_map.items():
        resp_bp = session.get(
            f"{svc.base_url}/BusinessPartners('{card_code}')",
            params={"$select": "CardCode,CardName,EmailAddress"},
            timeout=8.0,
        )
        if resp_bp.status_code != 200:
            logger.warning("PN %s não encontrado no SAP", card_code)
            continue
        bp = resp_bp.json()
        email = (bp.get("EmailAddress") or "").strip().lower()
        if not email:
            logger.warning("PN %s (%s) não possui EmailAddress cadastrado", card_code, bp.get("CardName"))
            continue

        regs = sorted(list(info["regioes"]))
        qtd = len(regs)
        eh_multiplo = qtd > 1
        if info["tipo"] == "representante":
            rotulo = regs[0] if regs else "—"
            detalhe = regs[0] if regs else "—"
        else:
            rotulo = regs[0] if qtd == 1 else f"{qtd} regiões vinculadas"
            detalhe = ", ".join(regs)

        registros_finais.append({
            "email": email,
            "card_code": card_code,
            "card_name": bp.get("CardName"),
            "tipo": info["tipo"],
            "rotulo": rotulo,
            "regioes": regs,
            "detalhe": detalhe,
            "eh_multiplo": eh_multiplo,
        })

    logger.info("Total de parceiros comerciais com e-mail válido: %d", len(registros_finais))

    # 3. Grava no banco Supabase
    async with admin_connection() as conn:
        for reg in registros_finais:
            await conn.execute(
                """
                INSERT INTO public.sap_regioes_autores (
                    email, card_code, card_name, tipo, rotulo, regioes, detalhe, eh_multiplo, atualizado_em
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
                ON CONFLICT (email) DO UPDATE SET
                    card_code = EXCLUDED.card_code,
                    card_name = EXCLUDED.card_name,
                    tipo = EXCLUDED.tipo,
                    rotulo = EXCLUDED.rotulo,
                    regioes = EXCLUDED.regioes,
                    detalhe = EXCLUDED.detalhe,
                    eh_multiplo = EXCLUDED.eh_multiplo,
                    atualizado_em = now()
                """,
                reg["email"],
                reg["card_code"],
                reg["card_name"],
                reg["tipo"],
                reg["rotulo"],
                reg["regioes"],
                reg["detalhe"],
                reg["eh_multiplo"],
            )

    logger.info("Backfill concluído com sucesso! %d parceiros inseridos/atualizados.", len(registros_finais))
    return True


if __name__ == "__main__":
    asyncio.run(executar_backfill())
```

- [ ] **Step 2: Commit do script**

```bash
git add scripts/backfill_sap_regioes.py
git commit -m "feat(scripts): script de backfill das regioes comerciais do SAP"
```

---

### Task 5: Execução do Backfill, Teste Real com Helmano (`BD-2026-01175`) e Documentação

**Files:**
- Execute: `scripts/backfill_sap_regioes.py`
- Modify: `docs/CHANGELOG.md`
- Modify: `plano_mestre_desenvolvimento.md`

- [ ] **Step 1: Executar o backfill real**

Run: `.venv\Scripts\python scripts/backfill_sap_regioes.py`  
Expected: Conectar ao SAP, carregar as regiões e salvar no Supabase sem erros.

- [ ] **Step 2: Validar consulta do chamado do Helmano (`BD-2026-01175`)**

Executar script de teste chamando `obter_regiao_do_autor("1d2f1b28-da53-4606-ba0b-5558f07ad725")` (ID do Helmano):
```bash
.venv\Scripts\python -c "import asyncio, dotenv; dotenv.load_dotenv(); from app.services.sap_regiao import obter_regiao_do_autor; print(asyncio.run(obter_regiao_do_autor('1d2f1b28-da53-4606-ba0b-5558f07ad725')))"
```
Expected: `RegiaoInfo(tipo='supervisor', rotulo='6 regiões vinculadas', regioes=[...], eh_multiplo=True)` retornado em milissegundos via banco de dados!

- [ ] **Step 3: Executar suíte completa de testes e linter**

Run:
```bash
.venv\Scripts\ruff check .
.venv\Scripts\pytest -q
```
Expected: All checks passed, 100% dos testes verdes.

- [ ] **Step 4: Atualizar `docs/CHANGELOG.md` e `plano_mestre_desenvolvimento.md`**

Registrar no topo do changelog a entrega do espelho durável de regiões em banco e fechar a demanda.

- [ ] **Step 5: Commit final**

```bash
git add docs/CHANGELOG.md plano_mestre_desenvolvimento.md
git commit -m "docs: registrar entrega de sincronizacao de regioes comerciais no changelog"
```

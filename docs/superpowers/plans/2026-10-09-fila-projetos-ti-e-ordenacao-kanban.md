# Fila de Projetos de TI e Ordenação Universal no Kanban — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implementar no Portal a visualização da fila de projetos de TI e a estimativa de entrega para gestores/líderes de setor (`role == 'ADMIN'`) ao selecionar a categoria "Desenvolvimento", e aplicar o algoritmo de ordenação (SLA primário + prioridade secundária em 24h) a todas as colunas de trabalho do Kanban de todos os setores.

**Architecture:** Módulo de domínio puro `app/domain/projetos.py` desacoplado e 100% testável para cálculo de estimativa e ordenação por SLA/prioridade; métodos dedicados em `FilaRepo`/`ChamadosRepo` com queries SQL seguras; rota HTMX `GET /portal/chamados/fila-projetos` renderizando fragmento server-side Jinja2 estilizado com Tailwind; e integração da ordenação nas colunas de `app/routes/workspace.py::kanban`.

**Tech Stack:** Python 3.12+, FastAPI, Jinja2, HTMX 2.0, Tailwind CSS 3.4, asyncpg / PostgreSQL (Supabase), Pytest.

## Global Constraints

- **Líder de setor (gestor):** restrito estritamente a usuários autenticados com `perfil.get("role") == "ADMIN"`.
- **Regime de SLA:** horário comercial (segunda a sexta, 08:00 às 18:00 no fuso `America/Sao_Paulo`), pausando em fins de semana e feriados nacionais (`feriados`), além de pausas no status `AGUARDANDO`. Todo o cálculo de dias é em **dias úteis**.
- **Algoritmo de Ordenação:** 
  1. Menor tempo restante de SLA (`limite_resolucao - agora`) no topo;
  2. Chamados com vencimento dentro de uma janela de 24h desempatados por Prioridade (`URGENTE` > `ALTA` > `MEDIA` > `BAIXA`);
  3. Chamados sem prazo (`limite_resolucao IS NULL` ou `sem_prazo == True`) no final, desempatados entre si por prioridade;
  4. Coluna `RESOLVIDO` preservada com ordenação por `resolvido_em DESC` (mais recentes primeiro).
- **Sem regressão de testes:** suíte existente (mais de 1100 testes) deve permanecer 100% verde com cobertura acima do piso do `pyproject.toml`.

---

### Task 1: Módulo de Domínio `app/domain/projetos.py` e Testes Unitários

**Files:**
- Create: `app/domain/projetos.py`
- Create: `tests/test_projetos_dominio.py`

**Interfaces:**
- Produces:
  - `chave_ordenacao_sla_prioridade(chamado: dict, agora: datetime) -> tuple`
  - `ordenar_chamados_sla_prioridade(chamados: list[dict], agora: datetime | None = None) -> list[dict]`
  - `calcular_estimativa_novo_projeto(projetos_ativos: list[dict], metricas_concluidos: dict | None, agora: datetime | None = None) -> dict`

- [ ] **Step 1: Escrever os testes unitários do módulo de domínio**

Criar `tests/test_projetos_dominio.py` com casos cobrindo:
1. Ordenação com chamado já vencido ficando à frente de chamado com prazo futuro.
2. Desempate por prioridade (`URGENTE` > `ALTA` > `MEDIA` > `BAIXA`) para chamados cuja diferença de SLA é menor que 24h.
3. Chamados com mais de 24h de diferença respeitando o SLA mesmo que o de menor prazo tenha prioridade mais baixa.
4. Chamados sem prazo (`limite_resolucao = None` ou `sem_prazo = True`) ficando no final da lista, desempatados por prioridade.
5. Estimativa de novo projeto calculando posição correta (`len(ativos) + 1`), dias úteis médios e data de entrega pulando fins de semana.

```python
from datetime import UTC, datetime, timedelta
import pytest
from app.domain.projetos import (
    chave_ordenacao_sla_prioridade,
    ordenar_chamados_sla_prioridade,
    calcular_estimativa_novo_projeto,
)

AGORA = datetime(2026, 10, 9, 10, 0, 0, tzinfo=UTC)  # Sexta-feira 10:00

def test_ordenacao_vencido_primeiro():
    c_vencido = {"id": "1", "limite_resolucao": AGORA - timedelta(hours=2), "prioridade": "BAIXA"}
    c_futuro = {"id": "2", "limite_resolucao": AGORA + timedelta(hours=10), "prioridade": "URGENTE"}
    ordenados = ordenar_chamados_sla_prioridade([c_futuro, c_vencido], agora=AGORA)
    assert ordenados[0]["id"] == "1"
    assert ordenados[1]["id"] == "2"

def test_desempate_prioridade_janela_24h():
    c_alta = {"id": "1", "limite_resolucao": AGORA + timedelta(hours=5), "prioridade": "ALTA"}
    c_urgente = {"id": "2", "limite_resolucao": AGORA + timedelta(hours=8), "prioridade": "URGENTE"}
    c_baixa = {"id": "3", "limite_resolucao": AGORA + timedelta(hours=2), "prioridade": "BAIXA"}
    # Todos dentro de 24h: URGENTE (id 2) > ALTA (id 1) > BAIXA (id 3)
    ordenados = ordenar_chamados_sla_prioridade([c_baixa, c_alta, c_urgente], agora=AGORA)
    assert [c["id"] for c in ordenados] == ["2", "1", "3"]

def test_diferenca_maior_24h_respeita_sla():
    c_amanha_baixa = {"id": "1", "limite_resolucao": AGORA + timedelta(hours=12), "prioridade": "BAIXA"}
    c_proxima_semana_urgente = {"id": "2", "limite_resolucao": AGORA + timedelta(hours=96), "prioridade": "URGENTE"}
    ordenados = ordenar_chamados_sla_prioridade([c_proxima_semana_urgente, c_amanha_baixa], agora=AGORA)
    assert ordenados[0]["id"] == "1"
    assert ordenados[1]["id"] == "2"

def test_sem_prazo_vai_para_o_final():
    c_sem_prazo = {"id": "1", "limite_resolucao": None, "prioridade": "URGENTE"}
    c_com_prazo = {"id": "2", "limite_resolucao": AGORA + timedelta(days=5), "prioridade": "BAIXA"}
    ordenados = ordenar_chamados_sla_prioridade([c_sem_prazo, c_com_prazo], agora=AGORA)
    assert ordenados[0]["id"] == "2"
    assert ordenados[1]["id"] == "1"

def test_estimativa_novo_projeto():
    ativos = [{"id": f"{i}"} for i in range(5)]
    metricas = {"total_concluidos": 54, "media_dias_reais": 7.5, "media_dias_sla": 12.5}
    est = calcular_estimativa_novo_projeto(ativos, metricas, agora=AGORA)
    assert est["posicao_fila"] == 6
    assert est["tma_dias_uteis"] == 7.5
    assert est["data_prevista"] is not None
    # Como AGORA é sexta-feira, a data prevista deve pular o fim de semana
    assert est["data_prevista"] > AGORA + timedelta(days=7)
```

- [ ] **Step 2: Executar o teste e verificar que falha**

Run: `pytest tests/test_projetos_dominio.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.domain.projetos'`.

- [ ] **Step 3: Implementar o módulo de domínio `app/domain/projetos.py`**

Criar `app/domain/projetos.py`:
```python
"""Domínio de projetos e algoritmo universal de ordenação (SLA + Prioridade)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
from typing import Any

PESOS_PRIORIDADE = {
    "URGENTE": 1,
    "ALTA": 2,
    "MEDIA": 3,
    "BAIXA": 4,
}

def chave_ordenacao_sla_prioridade(chamado: dict[str, Any], agora: datetime) -> tuple:
    """Gera chave de ordenação estável baseada em SLA (primário) e Prioridade (secundário em 24h).
    
    1. tem_prazo: 0 para chamados com limite_resolucao definido, 1 para sem prazo (vão pro fim).
    2. bloco_24h: agrupa por janela de 86400s (24h) a partir do momento atual.
    3. peso_prioridade: URGENTE (1) > ALTA (2) > MEDIA (3) > BAIXA (4).
    4. segundos_restantes: desempate fino pelo prazo exato.
    5. created_at: desempate cronológico.
    """
    limite = chamado.get("limite_resolucao")
    sem_prazo = chamado.get("sem_prazo") or False
    
    if not limite or sem_prazo:
        # Sem prazo: bloco infinito, desempate por prioridade e depois criação
        prioridade = str(chamado.get("prioridade") or "MEDIA").upper()
        peso_pr = PESOS_PRIORIDADE.get(prioridade, 3)
        created_at = chamado.get("created_at") or agora
        return (1, 999999, peso_pr, 0.0, created_at)
    
    delta_segundos = (limite - agora).total_seconds()
    # Janela de 24h (86400s)
    bloco_24h = math.floor(delta_segundos / 86400.0)
    
    prioridade = str(chamado.get("prioridade") or "MEDIA").upper()
    peso_pr = PESOS_PRIORIDADE.get(prioridade, 3)
    created_at = chamado.get("created_at") or agora
    
    return (0, bloco_24h, peso_pr, delta_segundos, created_at)

def ordenar_chamados_sla_prioridade(
    chamados: list[dict[str, Any]], agora: datetime | None = None
) -> list[dict[str, Any]]:
    """Ordena uma lista de chamados utilizando o critério de SLA + Prioridade em 24h."""
    agora = agora or datetime.now(UTC)
    return sorted(chamados, key=lambda c: chave_ordenacao_sla_prioridade(c, agora))

def calcular_estimativa_novo_projeto(
    projetos_ativos: list[dict[str, Any]],
    metricas_concluidos: dict[str, Any] | None,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Calcula a estimativa de tempo e posição para um novo projeto na fila."""
    agora = agora or datetime.now(UTC)
    posicao_fila = len(projetos_ativos) + 1
    
    tma_dias = 7.5
    if metricas_concluidos and metricas_concluidos.get("media_dias_reais"):
        tma_dias = float(metricas_concluidos["media_dias_reais"])
    
    # Estimativa de dias úteis baseada no TMA histórico e na vazão da fila
    # Cada projeto ativo à frente adiciona uma fração ponderada de tempo útil
    dias_uteis_estimados = max(round(tma_dias + (len(projetos_ativos) * 1.5)), 3)
    
    # Projetar data pulando fins de semana (sábado e domingo)
    cur = agora
    dias_adicionados = 0
    while dias_adicionados < dias_uteis_estimados:
        cur += timedelta(days=1)
        # 5 = Sábado, 6 = Domingo
        if cur.weekday() < 5:
            dias_adicionados += 1
            
    return {
        "posicao_fila": posicao_fila,
        "tma_dias_uteis": tma_dias,
        "dias_uteis_estimados": dias_uteis_estimados,
        "data_prevista": cur,
        "total_ativos": len(projetos_ativos),
    }
```

- [ ] **Step 4: Executar os testes e verificar que passam**

Run: `pytest tests/test_projetos_dominio.py -v`
Expected: PASS com 5 testes verdes.

- [ ] **Step 5: Commit**

```bash
git add app/domain/projetos.py tests/test_projetos_dominio.py
git commit -m "feat(projetos): modulo de dominio para ordenacao SLA/prioridade e estimativa"
```

---

### Task 2: Repositório de Fila e Chamados (`app/repositories/fila.py` e `chamados.py`)

**Files:**
- Modify: `app/repositories/fila.py:50-90`
- Modify: `app/repositories/chamados.py:340-370`

**Interfaces:**
- Produces:
  - `FilaRepo.fila_projetos_desenvolvimento(claims: dict, departamento_id: str | None) -> list[dict]`
  - `ChamadosRepo.fila_projetos_desenvolvimento(claims: dict, departamento_id: str | None) -> list[dict]`
  - `ChamadosRepo.metricas_projetos_desenvolvimento(claims: dict) -> dict`

- [ ] **Step 1: Escrever teste de repositório ou mock de interface**

Verificar no fake repo de testes `FakeRepo` em `tests/test_portal.py` a adição de `fila_projetos_desenvolvimento` e `metricas_projetos_desenvolvimento`.

- [ ] **Step 2: Implementar métodos em `app/repositories/fila.py`**

Adicionar em `app/repositories/fila.py`:
```python
    async def fila_projetos_desenvolvimento(
        self,
        claims: dict,
        *,
        departamento_id: str | None = None,
        limite: int = 50,
    ) -> list[dict[str, Any]]:
        """Busca projetos ativos de TI (categoria Desenvolvimento ou status PROJETOS)
        para compor a fila visual de projetos."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                self._FILA_COLUNAS
                + """
                 WHERE c.resolvido_em IS NULL
                   AND c.chamado_principal_id IS NULL
                   AND ($1::uuid IS NULL OR c.departamento_id = $1::uuid)
                   AND (cat.nome ILIKE '%Desenvolvimento%' OR c.status = 'PROJETOS')
                 ORDER BY c.limite_resolucao ASC NULLS LAST, c.created_at ASC
                 LIMIT $2
                """,
                departamento_id,
                limite,
            )
            return [dict(r) for r in rows]

    async def metricas_projetos_desenvolvimento(self, claims: dict) -> dict[str, Any]:
        """Calcula métricas históricas de projetos de desenvolvimento concluídos."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """SELECT 
                    COUNT(*)::int as total_concluidos,
                    COALESCE(AVG(EXTRACT(EPOCH FROM (c.resolvido_em - c.created_at))/86400)::numeric(10,1), 7.5)::float as media_dias_reais,
                    COALESCE(AVG(EXTRACT(EPOCH FROM (c.limite_resolucao - c.created_at))/86400)::numeric(10,1), 12.5)::float as media_dias_sla
                FROM chamados c
                JOIN categorias cat ON cat.id = c.categoria_id
                WHERE cat.nome ILIKE '%Desenvolvimento%'
                  AND c.resolvido_em IS NOT NULL
                """
            )
            return dict(row) if row else {"total_concluidos": 0, "media_dias_reais": 7.5, "media_dias_sla": 12.5}
```

- [ ] **Step 3: Expor fachada em `ChamadosRepo` (`app/repositories/chamados.py`)**

Adicionar os métodos delegando para `FilaRepo` no `ChamadosRepo`.

- [ ] **Step 4: Executar suíte de testes existente para garantir não-regressão**

Run: `pytest tests/test_fila_repo.py -v` (ou testes de repositório existentes).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/repositories/fila.py app/repositories/chamados.py
git commit -m "feat(repositories): metodos para consulta de fila e metricas de projetos de desenvolvimento"
```

---

### Task 3: Rota e Template do Painel Lateral de Projetos no Portal

**Files:**
- Create: `app/templates/portal/_fila_projetos_aside.html`
- Modify: `app/templates/portal/novo_chamado.html:260-280`
- Modify: `app/routes/portal.py:340-410`
- Modify: `tests/test_portal.py`

**Interfaces:**
- Consumes: `ChamadosRepo.fila_projetos_desenvolvimento`, `app.domain.projetos.ordenar_chamados_sla_prioridade`, `app.domain.projetos.calcular_estimativa_novo_projeto`
- Produces: Rota `GET /portal/chamados/fila-projetos`

- [ ] **Step 1: Escrever testes para a rota em `tests/test_portal.py`**

Adicionar testes:
1. `test_fila_projetos_cliente_bloqueado`: Usuário com role `CLIENTE` acessando a rota recebe `200` com corpo vazio (ou 403).
2. `test_fila_projetos_admin_categoria_desenvolvimento`: Usuário `ADMIN` acessando a rota com a categoria "Desenvolvimento" recebe o HTML do fragmento contendo os projetos ordenados e o bloco de estimativa.
3. `test_fila_projetos_admin_outra_categoria`: Usuário `ADMIN` acessando com outra categoria recebe conteúdo vazio.

- [ ] **Step 2: Executar o teste e verificar que falha**

Run: `pytest tests/test_portal.py -k "fila_projetos" -v`
Expected: FAIL com 404 (rota ainda não existe).

- [ ] **Step 3: Implementar a rota em `app/routes/portal.py`**

Adicionar a rota:
```python
@router.get("/chamados/fila-projetos")
async def fila_projetos_fragmento(
    request: Request,
    categoria_id: str = "",
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Fragmento HTMX para exibição da fila de projetos no painel lateral.
    Visível exclusivamente para gestores/líderes de setor (role == 'ADMIN')."""
    if ctx.perfil.get("role") != "ADMIN" or not categoria_id.strip():
        return Response("", media_type="text/html")
    
    nome_cat = await repo.nome_categoria(ctx.user.claims, categoria_id.strip())
    if not nome_cat or "desenvolvimento" not in nome_cat.lower():
        return Response("", media_type="text/html")
    
    projetos = await repo.fila_projetos_desenvolvimento(ctx.user.claims)
    projetos_ordenados = ordenar_chamados_sla_prioridade(projetos)
    
    metricas = await repo.metricas_projetos_desenvolvimento(ctx.user.claims)
    estimativa = calcular_estimativa_novo_projeto(projetos_ordenados, metricas)
    
    # Calcular estado visual de SLA para cada projeto
    for p in projetos_ordenados:
        p["sla_estado"] = estado_sla(p.get("created_at"), p.get("limite_resolucao"), status=p.get("status"))
        
    return render(
        request,
        "portal/_fila_projetos_aside.html",
        {
            "projetos": projetos_ordenados,
            "estimativa": estimativa,
        },
    )
```

- [ ] **Step 4: Criar o template `app/templates/portal/_fila_projetos_aside.html`**

Criar template Jinja2 estilizado com Tailwind com card, badge de contagem, caixa de estimativa, e lista scrollável com SLA visual.

- [ ] **Step 5: Integrar container no template `app/templates/portal/novo_chamado.html`**

Adicionar o container no `<aside>`:
```html
    {% if perfil.role == 'ADMIN' %}
    <div id="painel-fila-projetos"
         hx-get="/portal/chamados/fila-projetos"
         hx-trigger="change from:#categoria-select"
         hx-include="#categoria-select"
         hx-swap="innerHTML">
    </div>
    {% endif %}
```

- [ ] **Step 6: Executar os testes da rota e verificar que passam**

Run: `pytest tests/test_portal.py -k "fila_projetos" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routes/portal.py app/templates/portal/_fila_projetos_aside.html app/templates/portal/novo_chamado.html tests/test_portal.py
git commit -m "feat(portal): painel lateral da fila de projetos com estimativa para gestores"
```

---

### Task 4: Ordenação Universal no Kanban de Todos os Setores

**Files:**
- Modify: `app/routes/workspace.py:400-425`
- Modify: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `app.domain.projetos.ordenar_chamados_sla_prioridade`
- Modifies: `app.routes.workspace.py::kanban`

- [ ] **Step 1: Escrever teste em `tests/test_workspace.py` para ordenação do Kanban**

Criar teste que carrega o Kanban com múltiplos chamados na coluna `NOVO` e `PROJETOS` em setores como TI e Manutenção e valida que os cartões retornam ordenados pelo algoritmo SLA + Prioridade em 24h, enquanto a coluna `RESOLVIDO` segue por `resolvido_em DESC`.

- [ ] **Step 2: Executar o teste e verificar que falha**

Run: `pytest tests/test_workspace.py -k "ordenacao_kanban" -v`
Expected: FAIL.

- [ ] **Step 3: Aplicar o algoritmo de ordenação em `app/routes/workspace.py`**

Em `app/routes/workspace.py::kanban`:
```python
    colunas = {}
    for s in status_list:
        cards_coluna = [c for c in chamados if c["status"] == s]
        if s == "RESOLVIDO":
            cards_coluna.sort(key=lambda c: c.get("resolvido_em") or c.get("created_at"), reverse=True)
        else:
            cards_coluna = ordenar_chamados_sla_prioridade(cards_coluna)
        colunas[s] = cards_coluna
```

- [ ] **Step 4: Executar os testes e verificar que passam**

Run: `pytest tests/test_workspace.py -k "ordenacao_kanban" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/workspace.py tests/test_workspace.py
git commit -m "feat(kanban): ordenacao universal por SLA e prioridade em todas as colunas ativas"
```

---

### Task 5: Validação Completa de CI/CD e Qualidade

**Files:**
- Verify: todos os arquivos tocados

- [ ] **Step 1: Rodar linter ruff**

Run: `python -m ruff check .`
Expected: All checks passed.

- [ ] **Step 2: Rodar checagem estática mypy**

Run: `python -m mypy app/domain/projetos.py`
Expected: Success: no issues found.

- [ ] **Step 3: Rodar compilação do Tailwind**

Run: `npm run build:css`
Expected: Build concluído com sucesso sem erros.

- [ ] **Step 4: Rodar suíte de testes pytest completa com cobertura**

Run: `python -m pytest`
Expected: Todos os testes verdes (> 1150 testes) e cobertura acima do piso.

- [ ] **Step 5: Commit final de conclusão**

```bash
git commit --allow-empty -m "chore: validacao final de CI/CD para fila de projetos e ordenacao kanban"
```

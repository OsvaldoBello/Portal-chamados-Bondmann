# Especificação de Design — Fila de Projetos de TI e Algoritmo Universal de Ordenação (SLA + Prioridade)

- **Data:** 2026-10-09
- **Autor/Status:** Proposta Aprovada / Em Planejamento
- **Contexto:** Portal de Chamados Bondmann Química
- **Documento Mestre:** [plano_mestre_desenvolvimento.md](file:///c:/Users/Osvaldo/OneDrive/Desktop/Portal-chamados-Bondmann-claude-develop/plano_mestre_desenvolvimento.md)

---

## 1. Visão Geral e Objetivos

Com a crescente demanda por projetos e automações corporativas no setor de TI, faz-se necessário proporcionar aos gestores e líderes de setor transparência imediata sobre o pipeline de desenvolvimento antes mesmo da abertura de uma nova demanda.

Adicionalmente, identificou-se que o Kanban operacional de setores que não utilizam a data de entrega do Marketing (TI, RH, Dpto Químico, Manutenção) vinha ordenando cartões meramente por data de criação (`created_at DESC`), deixando de priorizar itens com estouro iminente de SLA.

### Objetivos Principais:
1. **Painel Lateral de Projetos na Abertura de Chamado:**
   - Exibir a fila de projetos de TI em andamento no painel lateral direito (`<aside>`) da tela [novo_chamado.html](file:///c:/Users/Osvaldo/OneDrive/Desktop/Portal-chamados-Bondmann-claude-develop/app/templates/portal/novo_chamado.html) quando o usuário for um líder de setor (`role == 'ADMIN'`) e selecionar a categoria **"Desenvolvimento"** (destino TI).
2. **Estimativa de Conclusão da Nova Demanda:**
   - Apresentar ao líder um cálculo preditivo com base nos projetos de desenvolvimento já concluídos (comparando o SLA previsto com o tempo real decorrido — TMA histórico), indicando a posição estimada na fila e uma previsão aproximada de entrega.
3. **Algoritmo Universal de Ordenação (SLA + Prioridade):**
   - Implementar ordenação por:
     - **1º Critério:** Tempo restante para estourar o SLA (`limite_resolucao - agora`), colocando no topo chamados já estourados ou mais próximos do vencimento.
     - **2º Critério:** Prioridade (`URGENTE` > `ALTA` > `MEDIA` > `BAIXA`) como desempate para chamados com vencimento dentro de uma mesma janela de **24 horas**.
     - **Chamados sem prazo:** Alocados no final, desempatados por prioridade.
   - Aplicar este algoritmo tanto à **Fila de Projetos** quanto a **todas as colunas ativas do Kanban de todos os setores** (`NOVO`, `PROJETOS`, `EM_ATENDIMENTO`, `AGUARDANDO`, etc.), preservando a coluna `RESOLVIDO` com ordenação por `resolvido_em DESC`.

---

## 2. Esclarecimento de Regra de Negócio: Regime de SLA (Dias Úteis)

Conforme estabelecido pela migration `0017_sla_horario_comercial.sql` e mantido pelas migrations `0064` e `0066`:
- **Expediente Comercial:** Segunda a sexta-feira, das **08:00 às 18:00** (horário de Brasília / `America/Sao_Paulo`), totalizando **10 horas úteis por dia de expediente**.
- **Finais de Semana:** Sábados e domingos são **completamente pausados** (0 horas contabilizadas).
- **Feriados Nacionais:** Consultados dinamicamente na tabela `feriados` (sincronizados via biblioteca `holidays` — `app/domain/feriados.py`), também **pausam** o relógio de SLA.
- **Pausa em Atendimento:** Status `AGUARDANDO` e `AGUARDANDO_TERCEIROS` congelam a contagem do SLA de resolução.
- **Conclusão:** **Todo prazo em dias de SLA no sistema é contado em DIAS ÚTEIS.** Portanto, 9 dias de SLA para conclusão equivalem a **9 dias úteis** (90 horas úteis de trabalho comercial), pulando fins de semana e feriados.

---

## 3. Modelo Matemático da Estimativa de Conclusão

Para um novo projeto submetido pelo gestor:
1. **Dados Históricos (Base Real):**
   - Amostra de chamados concluídos da categoria "Desenvolvimento":
     - $TMA_{real}$: Média do tempo real de resolução decorrido ($\text{resolvido\_em} - \text{created\_at}$ ou $\text{projeto\_em}$).
     - $SLA_{medio}$: Média do SLA estipulado no mesmo conjunto.
     - Fator de eficiência histórica: $\phi = \frac{TMA_{real}}{SLA_{medio}}$.
2. **Posição na Fila Atual:**
   - $N_{ativos}$: Quantidade de chamados ativos na fila de desenvolvimento.
   - Posição do novo projeto: $N_{ativos} + 1$.
3. **Projeção de Entrega:**
   - Base de execução padrão do setor: $P_{padrao}$ (dias úteis de projeto configurados no plano de SLA ou TMA histórico).
   - Data prevista estimada: calculada somando os dias úteis acumulados da fila ao momento atual utilizando `sla_add_minutos_uteis` (pulando fins de semana e feriados).

---

## 4. Arquitetura e Componentes

### 4.1 Módulo de Domínio (`app/domain/projetos.py`)

Criar módulo puro, determinístico e testável contendo:
- `chave_ordenacao_sla_prioridade(chamado: dict, agora: datetime) -> tuple`:
  - Retorna tupla para ordenação:
    1. `tem_prazo`: `0` se possui `limite_resolucao`, `1` se não possui (colocando sem prazo no fim).
    2. `bloco_sla`: `floor((limite_resolucao - agora).total_seconds() / 86400)` (agrupamento por janela de 24h).
    3. `peso_prioridade`: Invertido para ordenação crescente (`URGENTE`: 1, `ALTA`: 2, `MEDIA`: 3, `BAIXA`: 4).
    4. `segundos_exatos_sla`: `(limite_resolucao - agora).total_seconds()`.
    5. `created_at`: desempate cronológico.
- `calcular_estimativa_novo_projeto(projetos_ativos: list[dict], historico_concluidos: list[dict], agora: datetime) -> dict`:
  - Retorna:
    - `posicao_fila`: int
    - `tma_dias_uteis`: float
    - `data_prevista`: datetime | None
    - `texto_explicativo`: str

### 4.2 Repositório (`app/repositories/fila.py` e `chamados.py`)

- Adicionar método `fila_projetos_desenvolvimento(claims: dict, departamento_id: str | None) -> list[dict]`:
  - Busca chamados em aberto (`status NOT IN ('RESOLVIDO', 'CANCELADO')` e `chamado_principal_id IS NULL`) de TI vinculados à categoria "Desenvolvimento" ou com status `PROJETOS`.
  - Retorna dados essenciais: `id`, `codigo`, `titulo`, `status`, `prioridade`, `created_at`, `limite_resolucao`, `cliente_nome`, `setor`.
- Adicionar método `metricas_projetos_desenvolvimento(claims: dict) -> dict`:
  - Retorna quantidade de projetos concluídos, média de dias reais e média de dias de SLA para calibração da estimativa.

### 4.3 Rotas

#### Portal (`app/routes/portal.py`):
- `GET /portal/chamados/fila-projetos`:
  - Parâmetro: `categoria_id: str`.
  - Guarda de autorização: `ctx.perfil.get("role") == "ADMIN"`.
  - Verifica se a `categoria_id` informada corresponde a "Desenvolvimento" no departamento de TI.
  - Se não for "Desenvolvimento" ou se não for ADMIN, retorna resposta vazia (`200 OK` com conteúdo vazio para limpar o container HTMX).
  - Se for "Desenvolvimento", busca os projetos ativos, calcula a estimativa e renderiza o fragmento `portal/_fila_projetos_aside.html`.

#### Workspace / Kanban (`app/routes/workspace.py`):
- Na rota `GET /kanban`:
  - Atualizar o agrupamento de colunas para aplicar a ordenação `chave_ordenacao_sla_prioridade` em todas as colunas ativas (`NOVO`, `PROJETOS`, `EM_ATENDIMENTO`, `AGUARDANDO`, etc.).
  - A coluna `RESOLVIDO` permanece com `resolvido_em DESC` (mais recente primeiro).

### 4.4 Templates

1. **`app/templates/portal/novo_chamado.html`**:
   - Dentro de `<aside class="lg:sticky lg:top-6 space-y-4">`, inserir condicionalmente para `perfil.role == 'ADMIN'`:
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
2. **`app/templates/portal/_fila_projetos_aside.html`**:
   - Card lateral estilizado com Tailwind:
     - **Header:** Título "Fila de Projetos (TI)" + badge com contagem de projetos ativos.
     - **Bloco de Estimativa:** Box destacado com ícone de relógio, informando posição estimada (#X), tempo médio histórico de execução e previsão aproximada de conclusão.
     - **Lista de Projetos:** Container com scroll (`max-h-80 overflow-y-auto space-y-2.5`):
       - Posição (`#1`, `#2`...)
       - Código e título
       - Solicitante e setor
       - Badges de prioridade e status
       - Badge de SLA restante formatado (ex: *"Faltam 3d 4h"*, *"Vencido há 1d"*).

---

## 5. Casos de Teste e Validação

1. **Testes Unitários (`tests/test_projetos_dominio.py`)**:
   - Ordenação com projetos vencidos vs no prazo.
   - Desempate por prioridade dentro da mesma janela de 24h.
   - Chamados sem prazo posicionados no final.
   - Cálculo de dias úteis e estimativa de entrega pulando fins de semana e feriados.
2. **Testes de Integração de Rota (`tests/test_portal.py`)**:
   - Usuário `CLIENTE` ou `OPERADOR` requisitando `/portal/chamados/fila-projetos` recebe 403 ou fragmento vazio.
   - Usuário `ADMIN` requisitando categoria "Desenvolvimento" recebe o card formatado.
   - Requisitando outra categoria recebe fragmento vazio.
3. **Testes de Kanban (`tests/test_workspace.py`)**:
   - Verificação de que colunas ativas do Kanban em TI, RH e Manutenção ficam ordenadas por SLA + prioridade.
   - Verificação de que a coluna `RESOLVIDO` permanece ordenada por data de encerramento decrescente.

### 5.4 Validação no Pipeline de CI/CD (GitHub Actions)

As alterações devem passar por todos os gates automatizados do repositório ([.github/workflows/ci.yml](file:///c:/Users/Osvaldo/OneDrive/Desktop/Portal-chamados-Bondmann-claude-develop/.github/workflows/ci.yml) e [.github/workflows/e2e-rls.yml](file:///c:/Users/Osvaldo/OneDrive/Desktop/Portal-chamados-Bondmann-claude-develop/.github/workflows/e2e-rls.yml)):

1. **Job `pytest` (Suíte completa + Cobertura):**
   - Execução de todos os testes unitários e de integração sob Python 3.12 com `pytest-asyncio`.
   - Manutenção da métrica de cobertura de código acima do piso estipulado em `pyproject.toml` (`fail_under`).
2. **Job `ruff` (Linter & Formatter):**
   - Verificação de conformidade estrita de código com `python -m ruff check .` sem advertências nos novos arquivos.
3. **Job `mypy` (Checagem de Tipos Gradual):**
   - Verificação de tipagem estática nos novos módulos (`app/domain/projetos.py`).
4. **Job `build-css` (Tailwind CLI):**
   - Compilação estática do Tailwind (`npm run build:css`) garantindo que as classes utilitárias literais do novo fragmento `_fila_projetos_aside.html` sejam purgadas e incluídas no bundle sem erros de compilação.
5. **Job `docker-build` (Artefato de Produção):**
   - Validação de que a imagem Docker de produção compila integralmente sem quebras antes do deploy no Railway.
6. **Job `e2e-rls` (Matriz de Permissões RLS):**
   - Validação da matriz de segurança contra instância real do Supabase (`supabase start`), garantindo que o isolamento por departamento e as permissões de leitura de líderes (`0028`) não sofram regressão.

---

## 6. Critérios de Conclusão e Rollout

- [x] Regras de negócio alinhadas com o gestor (regime de dias úteis e desempate de 24h).
- [ ] Criação do módulo de domínio `app/domain/projetos.py` com testes unitários.
- [ ] Integração no repositório de chamados e fila.
- [ ] Implementação do endpoint e fragmento visual no portal de abertura.
- [ ] Aplicação da ordenação aprimorada no Kanban do Workspace.
- [ ] Suíte de testes local e gates de CI/CD (`pytest`, `ruff`, `mypy`, `build-css`, `docker-build`) verdes.

# Protótipo de Alta Fidelidade — Portal de Chamados Bondmann Química

Base visual de **todas as telas** do portal, construída como protótipo navegável de alta
fidelidade a partir de duas fontes:

- **Manual de Identidade Visual da Bondmann Química** (PANTONE 2955C / 367C, marca molecular, sub-marcas)
- **`plano_mestre_desenvolvimento.md`** (escopo funcional, papéis, SLA, multi-tenancy)

A stack usada no protótipo é a **mesma definida na Seção 0.4 do Plano Mestre**:
**Tailwind CSS 3.4 · HTMX · Alpine.js · Chart.js 4** — pronto para virar template Jinja2 no backend FastAPI.

## Como visualizar

Tudo é **estático e self-contained** (CSS, fontes e Chart.js estão em `assets/`, sem CDN em runtime).

```bash
# opção 1 — abrir direto no navegador
open prototipos/index.html        # macOS  (ou xdg-open / start)

# opção 2 — servidor local
npm run serve                     # http://localhost:8000
```

Comece por **`index.html`** — é a galeria que linka todas as telas.

## Telas (16)

| Grupo | Tela | Arquivo |
|---|---|---|
| Fundamentos | Design System (cores, tipografia, logo, componentes) | `design-system.html` |
| Autenticação | Login | `login.html` |
| Autenticação | Cadastro (solicitação de acesso) | `cadastro.html` |
| Autenticação | Recuperar senha (link 1h) | `recuperar-senha.html` |
| Cliente | Dashboard + meus chamados | `cliente-dashboard.html` |
| Cliente | Abrir chamado (form + upload + SLA previsto) | `cliente-novo-chamado.html` |
| Cliente | Detalhe do chamado + chat | `cliente-chamado-detalhe.html` |
| Operador | Fila — Lista (filtros, SLA visual, ações em lote) | `operador-fila-lista.html` |
| Operador | Fila — Kanban (drag & drop por status) | `operador-fila-kanban.html` |
| Operador | Atendimento (chat + **nota interna** + auditoria) | `operador-atendimento.html` |
| Admin | Dashboard / KPIs (gráficos Chart.js) | `admin-dashboard.html` |
| Admin | Empresas (tenants) | `admin-empresas.html` |
| Admin | Planos de SLA (tempos por prioridade) | `admin-planos-sla.html` |
| Admin | Categorias (catálogo global) | `admin-categorias.html` |
| Admin | Usuários (papéis e convites) | `admin-usuarios.html` |
| Admin | Relatórios (filtros + export CSV) | `admin-relatorios.html` |

## Decisões de design alinhadas ao Plano Mestre

- **RBAC visual** — três chrome de navegação distintos: `CLIENTE`, `OPERADOR`, `ADMIN` (Seção 3.2).
- **Status do chamado** — badges `NOVO / EM_ATENDIMENTO / AGUARDANDO / RESOLVIDO` (Seção 5.1).
- **SLA visual** — verde (no prazo) · amarelo (<25%) · **vermelho piscante** (vencido), conforme Fase 4.
- **Nota interna** — toggle com destaque amarelo; `is_interna` decidido no servidor (Seção 1.3 / 4).
- **Código do chamado** — formato `BOND-YYYY-NNNNN` (Seção 5.3).
- **Anexos** — aviso de *signed URL com TTL de 1h regenerada por visualização* (contradição C2).
- **SLA URGENTE = 50% de ALTA** e fallback global 12h/24h exibidos na tela de planos (C1).

## Mapeamento de fontes

O manual especifica **Info Book Italic** (títulos) e **Info Text** (texto) — fontes proprietárias
sem distribuição web. Foram mapeadas para equivalentes de alta qualidade e licença aberta:

| Uso no manual | Fonte web usada |
|---|---|
| Títulos / display / wordmark | **Archivo** (700–900) |
| Interface / texto | **Inter** (400–700) |

## Tokens da marca

Os mesmos tokens existem como **Figma Variables** no arquivo de design
(`Portal de Chamados — Bondmann Química`) e como tema do Tailwind em `tailwind.config.js`.

| Token | Hex | Origem |
|---|---|---|
| `navy` (institucional) | `#2E466F` | PANTONE 2955C |
| `brandgreen` | `#ACC76B` | PANTONE 367C |
| `ac_clean` / `ac_auto` / `ac_industry` / `ac_fluid` / `ac_max` / `ac_service` | `#1FB98C` `#41B6E6` `#5560B0` `#E63E62` `#F0934E` `#1B8A8F` | Sub-marcas |

## Como regenerar

As telas são geradas por um script Python (componentização DRY) e o CSS é compilado pelo Tailwind CLI:

```bash
npm install          # instala tailwindcss + chart.js (dev)
npm run build        # gera HTML (build.py) e recompila o app.css
```

- `build.py` — gerador das telas (helpers de logo, sidebar, badges, cards, tabelas, chat…).
- `tailwind.config.js` — tema com os tokens da marca.
- `assets/` — `app.css` (Tailwind compilado), `chart.umd.js`, `fonts.css` + `fonts/*.woff2`.

## Sobre o Figma

O design system foi **iniciado no Figma** (arquivo *Portal de Chamados — Bondmann Química*):
38 variáveis de cor da marca, estrutura de páginas e o componente do **logo molecular** já
criados. A continuação no Figma esbarrou no limite do **plano Starter (6 chamadas MCP/mês)**;
com upgrade para Professional o restante pode ser sincronizado a partir destes mesmos tokens.

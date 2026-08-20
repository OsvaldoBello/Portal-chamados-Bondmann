"""Exportação dos relatórios como **.html autocontido** com os indicadores visuais.

Pedido do usuário (2026-08-04): além do CSV (chamados brutos) e do XLSX do
Marketing (tabelas por indicador), poder baixar um `.html` que **se pareça com
o painel** — KPIs, gráficos e tabelas —, do jeito que os relatórios estáticos
que o time já circulava por e-mail eram feitos.

Decisões:

- **Autocontido de verdade.** O arquivo carrega o Chart.js embutido (o mesmo
  `app/static/vendor/chart.umd.js` já servido pelo painel, lido do disco e
  colado inline) e todo o CSS inline. Abre por duplo clique, de dentro de um
  anexo de e-mail ou de um pendrive, **sem internet e sem servidor** — um
  `<script src>` apontando para o Portal quebraria fora da rede/sessão, que é
  justamente onde um relatório exportado costuma ser aberto.
- **CSS escrito à mão aqui, não Tailwind.** É a única exceção deliberada à
  regra "sem CSS customizado" (Seção 0.1/C3 do plano mestre): o CSS compilado é
  purgado contra os templates do app e serve o shell; o arquivo exportado sai
  do domínio da aplicação e precisa carregar consigo apenas o que usa. As cores
  são as mesmas do painel.
- **Uma fonte de dados só.** Recebe exatamente os mesmos dicts que as telas
  usam (`AdminRepo.kpis`/`por_status`/... e `mkt_dashboard_data`), pelo mesmo
  motivo do `export_marketing.py`: o arquivo exportado nunca diverge do que
  está no navegador.
- **Gráficos declarativos.** Cada gráfico é montado aqui em Python como um
  dicionário (`_gráfico`), serializado num `<script type="application/json">` e
  instanciado por um laço genérico de ~15 linhas no HTML — em vez de repetir
  configuração de Chart.js por gráfico, como no `admin.js`/`admin_marketing.js`
  (que existem para telas interativas, com modal e filtro ao vivo).
- **Impressão.** `@media print` deixa o arquivo virar PDF direto pelo navegador
  (Ctrl+P → Salvar como PDF), que é como esse tipo de relatório costuma ser
  arquivado/enviado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.indicadores import resumir_satisfacao, resumir_tempo_conclusao
from app.domain.periodo import TZ_BR

# Paleta do painel (app/static/js/admin.js e tailwind.config.js).
NAVY = "#2E466F"
VERDE = "#7FA53D"
ROXO = "#7C3AED"
AMBAR = "#F59E0B"
VERMELHO = "#DC2626"
AZUL = "#2563EB"
TEAL = "#0D9488"
CINZA = "#94A3B8"
STATUS_COR = {
    "NOVO": AZUL,
    "A_FAZER": "#0EA5E9",
    "PROJETOS": ROXO,
    "EM_ATENDIMENTO": "#6366F1",
    "RESPOSTA_CLIENTE": TEAL,
    "AGUARDANDO_TERCEIROS": "#F97316",
    "AGUARDANDO": AMBAR,
    "RESOLVIDO": "#16A34A",
}
STATUS_LABEL = {
    "NOVO": "Novo",
    "A_FAZER": "A fazer",
    "PROJETOS": "Projetos",
    "EM_ATENDIMENTO": "Em atendimento",
    "RESPOSTA_CLIENTE": "Última interação do usuário",
    "AGUARDANDO_TERCEIROS": "Aguardando terceiros",
    "AGUARDANDO": "Aguardando",
    "RESOLVIDO": "Resolvido",
}

_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
_VENDOR_CHART = _STATIC_DIR / "vendor" / "chart.umd.js"
_APP_CSS = _STATIC_DIR / "css" / "app.css"
_ANIM_CSS = _STATIC_DIR / "css" / "anim.css"
_MKT_CSS = _STATIC_DIR / "css" / "dashboard_marketing.css"
_MKT_JS = _STATIC_DIR / "js" / "admin_marketing.js"
_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def _read_inline_script(path: Path) -> str:
    """JS lido do disco para ir inline no arquivo exportado — mesmo arquivo
    (mesma versão) que o painel serve, para o relatório nunca desenhar/agir
    diferente da tela por estar numa versão distinta. ``</script`` é
    neutralizado porque o conteúdo vai **dentro** de um ``<script>``: o parser
    HTML fecha a tag na primeira ocorrência literal, onde quer que ela esteja,
    inclusive dentro de uma string JS."""
    return path.read_text(encoding="utf-8").replace("</script", "<\\/script")


def _read_inline_style(path: Path) -> str:
    """Mesma ideia de `_read_inline_script`, para CSS dentro de um `<style>`."""
    return path.read_text(encoding="utf-8").replace("</style", "<\\/style")


def _chart_js() -> str:
    return _read_inline_script(_VENDOR_CHART)


def _grafico(
    ident: str,
    titulo: str,
    tipo: str,
    labels: list[Any],
    datasets: list[dict[str, Any]],
    *,
    subtitulo: str = "",
    horizontal: bool = False,
    legenda: bool = False,
    empilhado: bool = False,
    largura_total: bool = False,
    altura: int | None = None,
) -> dict[str, Any]:
    """Um cartão de gráfico do relatório, em formato declarativo.

    O HTML exportado não sabe o que é "CSAT" ou "volume": recebe esta lista e
    instancia `new Chart(...)` para cada item.

    A altura de gráfico **horizontal** cresce com o número de barras (o eixo Y
    vira a lista de rótulos): num ranking com 13 setores, uma altura fixa
    espreme as barras até virarem fios. Na tela isso não aparece porque cada
    aba tem sua caixa dimensionada à mão (`dashboard_marketing.css`); aqui é
    tudo um documento só."""
    if altura is None:
        altura = max(240, 26 * len(labels) + 60) if horizontal else 260
    return {
        "id": ident,
        "titulo": titulo,
        "subtitulo": subtitulo,
        "largura_total": largura_total,
        "altura": altura,
        "config": {
            "type": tipo,
            "data": {"labels": labels, "datasets": datasets},
            "options": {
                "responsive": True,
                "maintainAspectRatio": False,
                "animation": False,  # exportado é para ler/imprimir, não para animar
                "indexAxis": "y" if horizontal else "x",
                "plugins": {"legend": {"display": legenda, "position": "bottom"}},
                # `empilhado` é explícito (e não "toda barra com legenda"):
                # empilhar só faz sentido quando as séries são partes do mesmo
                # todo (concluídas + em andamento + abertas = as demandas do
                # mês). Em "demandas vs peças produzidas" ou "atendidos vs
                # resolvidos" as séries são grandezas distintas — empilhar
                # inventa um total que não existe.
                "scales": (
                    {}
                    if tipo in ("doughnut", "pie")
                    else {
                        "x": {"grid": {"display": False}, "stacked": empilhado},
                        "y": {"beginAtZero": True, "stacked": empilhado},
                    }
                ),
            },
        },
    }


def _barras(data: list[Any], cor: Any, rotulo: str = "") -> dict[str, Any]:
    # `maxBarThickness`: com poucas categorias (ex.: "por departamento" do TI, que
    # tem uma só) o Chart.js estica a barra até ocupar o cartão inteiro e o
    # gráfico vira um retângulo chapado.
    return {
        "label": rotulo,
        "data": data,
        "backgroundColor": cor,
        "borderRadius": 4,
        "maxBarThickness": 38,
    }


def _linha(data: list[Any], cor: str, rotulo: str = "") -> dict[str, Any]:
    return {
        "label": rotulo,
        "data": data,
        "borderColor": cor,
        "backgroundColor": cor,
        "tension": 0.3,
        "spanGaps": True,
        "fill": False,
    }


def _kpi(rotulo: str, valor: Any, nota: str = "", destaque: str = NAVY) -> dict[str, Any]:
    return {"rotulo": rotulo, "valor": valor, "nota": nota, "cor": destaque}


def _pct(parte: float, total: float) -> float:
    return round(100.0 * parte / total, 1) if total else 0.0


# ---------------------------------------------------------------------------
# Relatório do painel geral (/admin — TI, RH, demais setores)
# ---------------------------------------------------------------------------
def montar_relatorio_geral(
    *,
    escopo: str,
    periodo: str,
    kpis: dict[str, Any],
    graficos: dict[str, Any],
    avaliacoes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Contexto do relatório do painel geral — mesmos números da tela
    (`admin/dashboard.html`), no mesmo recorte de setor e mês."""
    por_status = graficos.get("por_status") or {}
    csat = graficos.get("csat") or {}
    por_departamento = graficos.get("por_departamento") or []
    por_setor = graficos.get("por_setor") or []
    produtividade = graficos.get("produtividade") or []
    tempo_conclusao = graficos.get("tempo_conclusao") or {
        "mesmo_dia": 0, "dia_seguinte": 0, "dois_dias_mais": 0,
    }
    # Mesmos resumos "fáceis de ler" da tela (2026-08-20) — ver
    # app/domain/indicadores.py: agrupam CSAT 1-5 e dias de conclusão em 3
    # faixas cada, usados aqui pro cartão extra e pela legenda dos 2 gráficos
    # novos abaixo.
    resumo_satisfacao = resumir_satisfacao(csat)
    resumo_tempo_conclusao = resumir_tempo_conclusao(**tempo_conclusao)

    cartoes = [
        _kpi("Chamados no total", kpis.get("total", 0)),
        _kpi("Abertos", kpis.get("abertos", 0), destaque=AZUL),
        _kpi(
            "Conformidade de SLA",
            f"{kpis['conformidade_sla']}%" if kpis.get("conformidade_sla") is not None else "—",
            "resolvidos dentro do prazo",
            VERDE,
        ),
        _kpi(
            "CSAT médio",
            kpis.get("csat_media") if kpis.get("csat_media") is not None else "—",
            (
                f"{kpis.get('csat_respostas')} de {kpis.get('resolvidos')} resolvidos avaliados"
                if kpis.get("csat_respostas")
                else "sem avaliações"
            ),
            VERDE,
        ),
        _kpi(
            "TMA (resolução)",
            f"{kpis['tma_horas']}h" if kpis.get("tma_horas") is not None else "—",
            "tempo médio",
        ),
        _kpi("Resolvidos", kpis.get("resolvidos", 0), destaque=VERDE),
        _kpi(
            "Resolvidos no mês de abertura",
            kpis.get("resolvidos_no_mes_abertura", 0),
            (
                f"{kpis.get('pct_resolvidos_no_mes_abertura')}% dos {kpis.get('total', 0)} abertos no mês"
                if kpis.get("pct_resolvidos_no_mes_abertura") is not None
                else "sem chamados abertos no mês"
            ),
            VERDE,
        ),
    ]
    if escopo == "TI":
        cartoes += [
            _kpi(
                "TMA (Projetos)",
                f"{kpis['tma_projetos_horas']}h"
                if kpis.get("tma_projetos_horas") is not None
                else "—",
                "separado do TMA geral",
                ROXO,
            ),
            _kpi("Projetos concluídos", kpis.get("projetos_resolvidos") or 0, destaque=ROXO),
        ]

    chaves_status = list(por_status.keys())
    charts = [
        _grafico(
            "status",
            "Chamados por status",
            "bar",
            [STATUS_LABEL.get(k, k.replace("_", " ")) for k in chaves_status],
            [_barras([por_status[k] for k in chaves_status],
                     [STATUS_COR.get(k, NAVY) for k in chaves_status])],
        ),
        _grafico(
            "csat",
            "Distribuição do CSAT (1–5)",
            "bar",
            ["1★", "2★", "3★", "4★", "5★"],
            [_barras([csat.get(n, csat.get(str(n), 0)) for n in range(1, 6)], VERDE)],
            subtitulo="Notas dos chamados resolvidos no período",
        ),
        _grafico(
            "departamento",
            "Chamados por departamento",
            "bar",
            [x["departamento"] for x in por_departamento],
            [_barras([x["total"] for x in por_departamento], NAVY)],
            horizontal=True,
        ),
        _grafico(
            "setor",
            "Chamados por setor solicitante",
            "bar",
            [x["setor"] for x in por_setor],
            [_barras([x["total"] for x in por_setor], NAVY)],
            horizontal=True,
        ),
        _grafico(
            "produtividade",
            "Produtividade por operador (resolvidos)",
            "bar",
            [x["operador"] for x in produtividade],
            [_barras([x["resolvidos"] for x in produtividade], VERDE)],
            horizontal=True,
            largura_total=True,
        ),
        _grafico(
            "tempo_conclusao",
            "Dias para conclusão",
            "bar",
            ["Mesmo dia", "Dia seguinte", "2 dias ou mais"],
            [_barras(
                [resumo_tempo_conclusao.mesmo_dia, resumo_tempo_conclusao.dia_seguinte,
                 resumo_tempo_conclusao.dois_dias_mais],
                [VERDE, AMBAR, VERMELHO],
            )],
            subtitulo=(
                f"{resumo_tempo_conclusao.pct_mesmo_dia}% no mesmo dia · "
                f"{resumo_tempo_conclusao.pct_dia_seguinte}% no dia seguinte"
                if resumo_tempo_conclusao.total
                else "Nenhum chamado resolvido no período"
            ),
        ),
        _grafico(
            "satisfacao",
            "Satisfação dos chamados",
            "bar",
            ["Satisfeito", "Neutro", "Insatisfeito"],
            [_barras(
                [resumo_satisfacao.satisfeito, resumo_satisfacao.neutro, resumo_satisfacao.insatisfeito],
                [VERDE, CINZA, VERMELHO],
            )],
            subtitulo=(
                f"{resumo_satisfacao.pct_satisfeito}% satisfeitos (nota 4–5)"
                if resumo_satisfacao.total
                else "Nenhuma avaliação no período"
            ),
        ),
    ]

    tabelas = []
    if avaliacoes:
        tabelas.append({
            "titulo": "Últimas avaliações",
            "subtitulo": "Feedback do solicitante (CSAT + comentário)",
            "colunas": ["Chamado", "Assunto", "Nota", "Comentário", "Solicitante"],
            "linhas": [
                [
                    a.get("codigo") or "—",
                    a.get("titulo") or "—",
                    "★" * int(a.get("nota") or 0),
                    a.get("comentario") or "—",
                    a.get("solicitante") or "—",
                ]
                for a in avaliacoes
            ],
        })

    return {
        "titulo": "Indicadores do Portal de Chamados",
        "escopo": escopo,
        "periodo": _rotulo_periodo(periodo),
        "kpis": cartoes,
        "charts": charts,
        "tabelas": tabelas,
    }


def _rotulo_periodo(periodo: str) -> str:
    """``YYYY-MM`` → "Agosto de 2026"; qualquer outra coisa volta como veio."""
    try:
        ano_s, mes_s = periodo.split("-", 1)
        ano, mes = int(ano_s), int(mes_s)
        if not 1 <= mes <= 12:  # índice negativo daria "dezembro" para mês 0
            raise ValueError(mes_s)
        return f"{_MESES[mes - 1].capitalize()} de {ano}"
    except (ValueError, AttributeError):
        return periodo or "—"


# ---------------------------------------------------------------------------
# Relatório do painel de Marketing (/admin com escopo Marketing)
# ---------------------------------------------------------------------------
def montar_relatorio_marketing(mkt_data: dict[str, Any], periodo: str = "all") -> dict[str, Any]:
    """Contexto do relatório de Marketing — clone autocontido da PRÓPRIA tela
    (`admin/dashboard_marketing.html`), não um resumo à parte num layout
    diferente (pedido do usuário 2026-08-05: "100% fiel [...] mantendo
    gráficos, estilo e etc da mesma forma vista no site").

    `mkt_data` viaja **inteiro, sem filtrar**: `admin_marketing.js` (colado
    inline pelo `renderizar()`) já faz toda a filtragem por período e a troca
    de aba no cliente a partir de `#mkt-data` — duplicar essa lógica aqui em
    Python é como o relatório antigo divergia da tela (chart diferente, cores
    diferentes, sem os plugins de pill/rótulo dos gráficos).

    ``periodo`` = "all" (Acumulado) ou o rótulo de um mês ("JUL/26") — o
    filtro que estava ativo na tela no momento do clique em "Exportar". Vira
    apenas o pill pré-selecionado no arquivo aberto (o template simula o
    clique nele via JS), não um recorte feito aqui. Rótulo desconhecido
    (querystring editada à mão, ou o botão genérico do painel mandando um
    `YYYY-MM` para quem é do Marketing) cai em "all": os rótulos são um
    conjunto fechado vindo do backend."""
    rotulos = {m["label"] for m in mkt_data.get("monthly", [])}
    if periodo != "all" and periodo not in rotulos:
        periodo = "all"

    return {
        "tipo": "marketing_fiel",
        "titulo": "Indicadores de Marketing",
        "escopo": "Marketing",
        "periodo": "Acumulado" if periodo == "all" else periodo,
        "periodo_filtro": periodo,
        "mkt_data": mkt_data,
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def renderizar(contexto: dict[str, Any]) -> str:
    """Renderiza o relatório como HTML autocontido (string pronta pra resposta).

    Marketing usa um template próprio — clone da tela, ver
    `montar_relatorio_marketing`. Os demais escopos seguem o relatório
    genérico de KPIs/gráficos/tabelas (`montar_relatorio_geral`)."""
    from app.templating import templates

    gerado_em = datetime.now(TZ_BR).strftime("%d/%m/%Y às %H:%M")

    if contexto.get("tipo") == "marketing_fiel":
        template = templates.env.get_template("admin/relatorio_marketing_export.html")
        return template.render(
            **contexto,
            app_css=_read_inline_style(_APP_CSS),
            anim_css=_read_inline_style(_ANIM_CSS),
            marketing_css=_read_inline_style(_MKT_CSS),
            chart_js=_chart_js(),
            admin_marketing_js=_read_inline_script(_MKT_JS),
            gerado_em=gerado_em,
        )

    template = templates.env.get_template("admin/relatorio_export.html")
    return template.render(
        **contexto,
        chart_js=_chart_js(),
        # Serializado no template com `|tojson` (que escapa `<`/`>`/`&` como
        # \uXXXX) — texto vindo do banco, ex. o assunto de um chamado, entra
        # aqui e não pode fechar a tag `<script>`.
        charts_config=[{"id": g["id"], "config": g["config"]} for g in contexto["charts"]],
        gerado_em=gerado_em,
    )


def nome_arquivo(escopo: str, periodo: str) -> str:
    """`indicadores_marketing_2026-08_20260804.html` — escopo e recorte no nome,
    porque esse arquivo vai virar anexo de e-mail e conviver com os anteriores."""
    limpo = "".join(ch if ch.isalnum() else "-" for ch in f"{escopo}_{periodo}".lower())
    return f"indicadores_{limpo.strip('-')}_{datetime.now(UTC):%Y%m%d}.html"

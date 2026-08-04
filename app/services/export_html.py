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

from app.domain.periodo import TZ_BR
from app.services.export_marketing import filtrar_por_periodo

# Paleta do painel (app/static/js/admin.js e tailwind.config.js).
NAVY = "#2E466F"
VERDE = "#7FA53D"
ROXO = "#7C3AED"
AMBAR = "#F59E0B"
VERMELHO = "#DC2626"
AZUL = "#2563EB"
TEAL = "#0D9488"
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

_VENDOR_CHART = Path(__file__).resolve().parents[1] / "static" / "vendor" / "chart.umd.js"
_MESES = (
    "janeiro", "fevereiro", "março", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def _chart_js() -> str:
    """Bundle do Chart.js lido do disco para ir inline no arquivo exportado.

    O mesmo arquivo (mesma versão, mesmo SRI) que o painel serve — o relatório
    não pode desenhar diferente da tela por estar numa versão distinta.
    ``</script`` é neutralizado porque o bundle vai **dentro** de um
    ``<script>``: o parser HTML fecha a tag na primeira ocorrência literal,
    onde quer que ela esteja, inclusive dentro de uma string JS."""
    return _VENDOR_CHART.read_text(encoding="utf-8").replace("</script", "<\\/script")


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
    """Contexto do relatório de Marketing — mesmas 7 abas da tela, achatadas em
    seções de um documento. ``periodo`` = "all" (Acumulado) ou o rótulo de um
    mês ("JUL/26"), exatamente como no `.xlsx` (`filtrar_por_periodo` é
    reaproveitado para os dois formatos não divergirem).

    Rótulo desconhecido (querystring editada à mão, ou o botão genérico do
    painel mandando um `YYYY-MM` para quem é do Marketing) cai em "all": os
    rótulos são um conjunto fechado, então um valor fora dele é erro de entrada
    — e filtrar por ele devolveria um relatório inteiro zerado, que parece um
    mês sem movimento em vez de um filtro inválido."""
    rotulos = {m["label"] for m in mkt_data.get("monthly", [])}
    if periodo != "all" and periodo not in rotulos:
        periodo = "all"
    dados = filtrar_por_periodo(mkt_data, periodo)
    monthly = dados["monthly"]
    labels = [m["label"] for m in monthly]

    total = sum(m["total"] for m in monthly)
    concluidas = sum(m["concluidas"] for m in monthly)
    em_andamento = sum(m["em_andamento"] for m in monthly)
    abertas = sum(m["abertas"] for m in monthly)
    volume = sum(m["volume"] for m in monthly)
    mkt_orig = sum(m["mkt_orig"] for m in monthly)
    atrasos = dados["atrasosData"]
    # Média ponderada por `concluidas`, ignorando os meses **sem dado** nos dois
    # lados da fração — `tempo_medio` vem `None` (não 0.0) quando o mês não teve
    # nenhuma conclusão, e dividir pelo total de concluídas do período puxaria a
    # média pra baixo. Mesma conta do `mediaTempoPonderada` do
    # `admin_marketing.js`, para o relatório não divergir da tela.
    peso = sum(m["concluidas"] for m in monthly if m["tempo_medio"] is not None)
    tempo_medio = (
        sum(m["tempo_medio"] * m["concluidas"] for m in monthly if m["tempo_medio"] is not None)
        / peso
        if peso
        else None
    )

    ranking: dict[str, int] = {}
    for por_setor in dados["deptByMonth"].values():
        for setor, qtd in por_setor.items():
            ranking[setor] = ranking.get(setor, 0) + qtd
    ranking_ord = sorted(ranking.items(), key=lambda kv: kv[1], reverse=True)

    causas: dict[str, int] = {}
    for a in atrasos:
        causas[a["causa"]] = causas.get(a["causa"], 0) + 1
    causas_ord = sorted(causas.items(), key=lambda kv: kv[1], reverse=True)

    # Operadores: soma dos meses do recorte (o dashboard mostra por mês; num
    # relatório do período acumulado o total é o que interessa).
    operadores: dict[str, dict[str, int]] = {}
    for rotulo in labels:
        for nome, v in (mkt_data.get("operadorByMonth", {}).get(rotulo) or {}).items():
            alvo = operadores.setdefault(nome, {"atendidos": 0, "resolvidos": 0})
            alvo["atendidos"] += v["atendidos"]
            alvo["resolvidos"] += v["resolvidos"]
    operadores_ord = sorted(operadores.items(), key=lambda kv: kv[1]["atendidos"], reverse=True)

    midia = dados["midia"]

    cartoes = [
        _kpi("Total de demandas", total),
        _kpi("Concluídas", concluidas, f"{_pct(concluidas, total)}% do total", VERDE),
        _kpi("Em andamento", em_andamento, destaque=AZUL),
        _kpi("Abertas", abertas, destaque=AMBAR),
        _kpi("Volume produzido", volume, "peças/cards", ROXO),
        _kpi(
            "Tempo médio de entrega",
            f"{round(tempo_medio, 1)} d" if tempo_medio is not None else "—",
            "só concluídas",
            VERDE,
        ),
        _kpi("Atrasos > 5 dias", len(atrasos), f"{_pct(len(atrasos), total)}% do total", VERMELHO),
        _kpi("Origem Marketing", mkt_orig, f"{_pct(mkt_orig, total)}% das demandas", ROXO),
    ]

    charts = [
        _grafico(
            "entrega",
            "Status das demandas — histórico mensal",
            "bar",
            labels,
            [
                _barras([m["concluidas"] for m in monthly], "#16A34A", "Concluídas"),
                _barras([m["em_andamento"] for m in monthly], "#6366F1", "Em andamento"),
                _barras([m["abertas"] for m in monthly], AMBAR, "Abertas"),
            ],
            subtitulo="Total de demandas recebidas por mês e status",
            legenda=True,
            empilhado=True,  # as três séries somam o total do mês
            largura_total=True,
        ),
        _grafico(
            "volume",
            "Demandas vs volume produzido",
            "bar",
            labels,
            [
                _barras([m["total"] for m in monthly], NAVY, "Demandas"),
                _barras([m["volume"] for m in monthly], ROXO, "Peças/cards"),
            ],
            subtitulo="Quantidade de demandas vs total de peças produzidas",
            legenda=True,
        ),
        _grafico(
            "origem",
            "Origem da demanda",
            "bar",
            labels,
            [
                _barras([m["mkt_orig"] for m in monthly], ROXO, "Marketing"),
                _barras([m["sol_orig"] for m in monthly], NAVY, "Outros setores"),
            ],
            subtitulo="Criadas pelo próprio Marketing vs solicitações",
            legenda=True,
        ),
        _grafico(
            "solicitantes",
            "Demandas por setor solicitante",
            "bar",
            [s for s, _ in ranking_ord],
            [_barras([q for _, q in ranking_ord], NAVY)],
            subtitulo="Ranking acumulado do período",
            horizontal=True,
        ),
        _grafico(
            "tempo",
            "Tempo médio de entrega (dias)",
            "line",
            labels,
            [
                _linha([m["tempo_medio"] for m in monthly], VERDE, "Tempo médio"),
                _linha([5 for _ in monthly], VERMELHO, "Limite (5 dias)"),
            ],
            subtitulo="Apenas demandas concluídas",
            legenda=True,
        ),
    ]
    if causas_ord:
        charts.append(
            _grafico(
                "causas",
                "Causas de atraso",
                "doughnut",
                [c for c, _ in causas_ord],
                [{
                    "data": [q for _, q in causas_ord],
                    "backgroundColor": [VERMELHO, AMBAR, ROXO, NAVY, TEAL, AZUL, VERDE],
                }],
                subtitulo="Distribuição das causas em atrasos > 5 dias",
                legenda=True,
            )
        )
    if operadores_ord:
        charts.append(
            _grafico(
                "operadores",
                "Chamados por operador",
                "bar",
                [n for n, _ in operadores_ord],
                [
                    _barras([v["atendidos"] for _, v in operadores_ord], NAVY, "Atendidos"),
                    _barras([v["resolvidos"] for _, v in operadores_ord], VERDE, "Resolvidos"),
                ],
                subtitulo="Só de chamados do Portal (meses pré-sistema não têm essa quebra)",
                legenda=True,
                largura_total=True,
            )
        )
    if midia["meses"]:
        charts.append(
            _grafico(
                "midia_indicadores",
                "Mídia regional — indicadores por mês",
                "bar",
                midia["meses"],
                [
                    _barras(midia["regioes"], NAVY, "Regiões ativas"),
                    _barras(midia["descontinuidades"], VERMELHO, "Descontinuidades"),
                    _barras(midia["aderencias"], VERDE, "Aderências"),
                ],
                legenda=True,
            )
        )
        charts.append(
            _grafico(
                "midia_investimento",
                "Investimento BD (R$)",
                "line",
                midia["meses"],
                [_linha(midia["investimento"], ROXO, "Investimento")],
                subtitulo="Valores mensais de veiculação regional",
            )
        )

    tabelas = [{
        "titulo": "Histórico mensal",
        "subtitulo": "Taxa de entrega e volume produzido",
        "colunas": [
            "Mês", "Total", "Concluídas", "Em andamento", "Abertas",
            "Volume", "Origem MKT", "Tempo médio (d)", "Atrasos > 5d",
        ],
        "linhas": [
            [
                m["label"], m["total"], m["concluidas"], m["em_andamento"], m["abertas"],
                m["volume"], m["mkt_orig"],
                "—" if m["tempo_medio"] is None else round(m["tempo_medio"], 1),
                m["atrasos"],
            ]
            for m in monthly
        ],
    }]
    if atrasos:
        tabelas.append({
            "titulo": "Demandas com atraso > 5 dias",
            "subtitulo": "Causas registradas no atendimento",
            "colunas": ["Demanda", "Mês", "Dias em aberto", "Causa"],
            "linhas": [[a["nome"], a["mes"], a["dias"], a["causa"]] for a in atrasos],
        })

    return {
        "titulo": "Indicadores de Marketing",
        "escopo": "Marketing",
        "periodo": "Acumulado" if periodo == "all" else periodo,
        "kpis": cartoes,
        "charts": charts,
        "tabelas": tabelas,
    }


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def renderizar(contexto: dict[str, Any]) -> str:
    """Renderiza o relatório como HTML autocontido (string pronta pra resposta)."""
    from app.templating import templates

    template = templates.env.get_template("admin/relatorio_export.html")
    return template.render(
        **contexto,
        chart_js=_chart_js(),
        # Serializado no template com `|tojson` (que escapa `<`/`>`/`&` como
        # \uXXXX) — texto vindo do banco, ex. o assunto de um chamado, entra
        # aqui e não pode fechar a tag `<script>`.
        charts_config=[{"id": g["id"], "config": g["config"]} for g in contexto["charts"]],
        gerado_em=datetime.now(TZ_BR).strftime("%d/%m/%Y às %H:%M"),
    )


def nome_arquivo(escopo: str, periodo: str) -> str:
    """`indicadores_marketing_2026-08_20260804.html` — escopo e recorte no nome,
    porque esse arquivo vai virar anexo de e-mail e conviver com os anteriores."""
    limpo = "".join(ch if ch.isalnum() else "-" for ch in f"{escopo}_{periodo}".lower())
    return f"indicadores_{limpo.strip('-')}_{datetime.now(UTC):%Y%m%d}.html"

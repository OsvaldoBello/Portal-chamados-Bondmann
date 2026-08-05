"""Exportação dos relatórios em .html com indicadores visuais (Fase 5).

O contrato que estes testes protegem é o de um arquivo que sai do domínio da
aplicação: ele é baixado, vai por e-mail e é aberto **sem servidor, sem sessão
e possivelmente sem internet**. Se ele passar a depender de `/static`, de CDN
ou de fonte remota, os gráficos somem no destinatário e ninguém percebe até
alguém reclamar.
"""

from __future__ import annotations

import re

from app.services.export_html import (
    montar_relatorio_geral,
    montar_relatorio_marketing,
    nome_arquivo,
    renderizar,
)

KPIS = {
    "total": 10, "abertos": 4, "resolvidos": 6, "conformidade_sla": 83.3,
    "csat_media": 4.5, "csat_respostas": 4, "tma_horas": 12.0,
    "projetos_resolvidos": 1, "tma_projetos_horas": 80.0,
}
GRAFICOS = {
    "por_status": {"NOVO": 2, "EM_ATENDIMENTO": 2, "RESOLVIDO": 6},
    "csat": {1: 0, 2: 0, 3: 1, 4: 1, 5: 2},
    "por_departamento": [{"departamento": "TI", "total": 7}],
    "por_setor": [{"setor": "Financeiro", "total": 6}],
    "produtividade": [{"operador": "Op TI", "resolvidos": 6}],
}
AVALIACOES = [{
    "codigo": "BOND-2026-00001", "titulo": "Impressora", "nota": 5,
    "comentario": "Ótimo atendimento", "solicitante": "Ana",
}]

MKT = {
    "monthly": [
        {"label": "JUN/26", "total": 4, "concluidas": 2, "em_andamento": 1, "abertas": 1,
         "volume": 6, "mkt_orig": 1, "sol_orig": 3, "tempo_medio": 2.0, "atrasos": 0},
        {"label": "JUL/26", "total": 10, "concluidas": 8, "em_andamento": 1, "abertas": 1,
         "volume": 25, "mkt_orig": 4, "sol_orig": 6, "tempo_medio": 3.5, "atrasos": 2},
    ],
    "deptByMonth": {"JUN/26": {"RH": 4}, "JUL/26": {"RH": 6, "Comercial": 4}},
    "operadorByMonth": {
        "JUN/26": {"Gabriela": {"atendidos": 4, "resolvidos": 2}},
        "JUL/26": {"Gabriela": {"atendidos": 7, "resolvidos": 6},
                   "Felipe": {"atendidos": 3, "resolvidos": 2}},
    },
    "atrasosData": [
        {"nome": "Banner novo", "mes": "JUL/26", "dias": 9, "causa": "Aguardando arte"},
        {"nome": "Catálogo", "mes": "JUL/26", "dias": 7, "causa": "Aguardando arte"},
    ],
    "midia": {"meses": ["Jun", "Jul"], "investimento": [1000.0, 1500.0],
              "regioes": [10, 12], "descontinuidades": [1, 0], "aderencias": [2, 3]},
}


def _geral():
    return montar_relatorio_geral(
        escopo="TI", periodo="2026-07", kpis=KPIS, graficos=GRAFICOS, avaliacoes=AVALIACOES
    )


# ---------------------------------------------------------------------------
# Conteúdo — os números do relatório são os da tela
# ---------------------------------------------------------------------------
def test_relatorio_geral_traz_os_kpis_do_painel():
    ctx = _geral()
    valores = {k["rotulo"]: k["valor"] for k in ctx["kpis"]}
    assert valores["Chamados no total"] == 10
    assert valores["Conformidade de SLA"] == "83.3%"
    assert valores["TMA (resolução)"] == "12.0h"
    assert valores["CSAT médio"] == 4.5
    # TMA/Projetos só existem no TI (única fila com a coluna "Projetos").
    assert valores["TMA (Projetos)"] == "80.0h"


def test_relatorio_geral_fora_do_ti_nao_tem_kpi_de_projetos():
    ctx = montar_relatorio_geral(
        escopo="RH", periodo="2026-07", kpis=KPIS, graficos=GRAFICOS, avaliacoes=[]
    )
    rotulos = [k["rotulo"] for k in ctx["kpis"]]
    assert "TMA (Projetos)" not in rotulos
    assert not ctx["tabelas"]  # sem avaliações, sem tabela vazia pendurada


def test_relatorio_geral_sem_metrica_mostra_travessao_em_vez_de_none():
    vazio = dict(KPIS, conformidade_sla=None, csat_media=None, tma_horas=None, csat_respostas=0)
    ctx = montar_relatorio_geral(
        escopo="RH", periodo="2026-07", kpis=vazio, graficos=GRAFICOS, avaliacoes=[]
    )
    valores = {k["rotulo"]: k["valor"] for k in ctx["kpis"]}
    assert valores["Conformidade de SLA"] == "—"
    assert valores["CSAT médio"] == "—"
    assert valores["TMA (resolução)"] == "—"


def test_relatorio_geral_periodo_vira_mes_por_extenso():
    assert _geral()["periodo"] == "Julho de 2026"


def test_status_do_grafico_saem_rotulados_e_coloridos_por_status():
    grafico = next(g for g in _geral()["charts"] if g["id"] == "status")
    assert grafico["config"]["data"]["labels"] == ["Novo", "Em atendimento", "Resolvido"]
    assert grafico["config"]["data"]["datasets"][0]["data"] == [2, 2, 6]
    # Cores por status (mesma paleta do painel), não uma cor só.
    assert len(set(grafico["config"]["data"]["datasets"][0]["backgroundColor"])) == 3


def test_csat_cobre_as_cinco_notas_mesmo_sem_avaliacao_em_alguma():
    grafico = next(g for g in _geral()["charts"] if g["id"] == "csat")
    assert grafico["config"]["data"]["datasets"][0]["data"] == [0, 0, 1, 1, 2]


# ---------------------------------------------------------------------------
# Marketing — clone autocontido da própria tela (não um resumo à parte)
# ---------------------------------------------------------------------------
# `montar_relatorio_marketing` não recorta/agrega mais nada em Python: o
# `admin_marketing.js` embutido pelo `renderizar()` já faz a filtragem por
# período e a troca de aba no cliente, a partir de `mkt_data` sem filtrar
# (pedido do usuário 2026-08-05: "100% fiel [...] mantendo gráficos, estilo e
# etc da mesma forma vista no site"). Estes testes protegem o contrato novo:
# os dados brutos passam intactos, e o `periodo` só vira o pill pré-selecionado.
def test_relatorio_marketing_passa_mkt_data_sem_filtrar():
    ctx = montar_relatorio_marketing(MKT, "JUN/26")
    assert ctx["tipo"] == "marketing_fiel"
    assert ctx["mkt_data"] is MKT  # nenhum recorte em Python — o JS filtra no cliente
    assert ctx["periodo"] == "JUN/26"
    assert ctx["periodo_filtro"] == "JUN/26"


def test_relatorio_marketing_acumulado():
    ctx = montar_relatorio_marketing(MKT, "all")
    assert ctx["periodo"] == "Acumulado"
    assert ctx["periodo_filtro"] == "all"


def test_marketing_periodo_desconhecido_cai_em_acumulado():
    """Rótulo fora do conjunto fechado (querystring editada, ou o `YYYY-MM` do
    botão genérico do painel chegando pelo desvio do Marketing) não pode virar
    um relatório filtrado por um pill que não existe na tela."""
    ctx = montar_relatorio_marketing(MKT, "2026-08")
    assert ctx["periodo"] == "Acumulado"
    assert ctx["periodo_filtro"] == "all"


# ---------------------------------------------------------------------------
# Render do relatório de Marketing — mesmo HTML/CSS/JS da tela, autocontido
# ---------------------------------------------------------------------------
def _marketing_html(periodo: str = "all") -> str:
    return renderizar(montar_relatorio_marketing(MKT, periodo))


def test_html_marketing_nao_referencia_nenhum_recurso_externo():
    html = _marketing_html()
    assert not re.search(r"<script[^>]+\bsrc=", html)
    assert not re.search(r"<link[^>]+\bhref=", html)
    assert not re.search(r"<img[^>]+\bsrc=", html)
    assert "@import" not in html
    assert not re.search(r"url\(\s*['\"]?(https?:)?//", html)
    assert not re.search(r"""\b(src|href)=['"](https?:)?//""", html)


def test_html_marketing_tem_os_mesmos_ids_que_admin_marketing_js_espera():
    """`admin_marketing.js` acessa vários destes ids sem checagem de nulo
    (ex.: `document.getElementById("header-title").textContent = ...`) — se um
    faltar, o script quebra no meio e as abas seguintes nem renderizam."""
    html = _marketing_html()
    for elemento_id in (
        "header-title", "filter-period-label", "sum-left", "sum-right",
        "chartEntrega", "chartVolume", "chartOrigem", "chartDept", "chartTempo",
        "chartCausas", "chartMidiaIndicadores", "chartMidiaInvest", "chartOperador",
        "dept-aviso", "atrasos-tbody", "operador-aviso", "mkt-data",
    ):
        assert f'id="{elemento_id}"' in html, elemento_id


def test_html_marketing_leva_o_mesmo_js_da_tela_embutido():
    html = _marketing_html()
    assert "Chart.js" in html  # licença do bundle, colado inline
    assert "function renderAllCharts" in html  # admin_marketing.js real, não uma cópia resumida
    assert "filter-pill" in html


def test_html_marketing_pre_seleciona_o_pill_do_periodo_ativo_na_tela():
    html = _marketing_html("JUN/26")
    assert '"JUN/26"' in html  # periodo_filtro serializado pro script de clique automático
    assert 'data-filter="JUN/26"' in html


def test_html_marketing_nao_leva_acoes_que_nao_funcionam_offline():
    """Upload de planilha e os botões "Exportar" fazem POST/GET pro servidor —
    não fazem sentido num arquivo baixado, sem sessão e possivelmente offline.
    (`admin_marketing.js` embutido ainda referencia esses ids nos handlers de
    filtro — de forma defensiva, `if (btnExportar) {...}` — então a string
    aparece no bundle de JS; o que não pode existir é o elemento em si.)"""
    html = _marketing_html()
    assert "marketing-midia/upload" not in html
    assert 'id="btn-exportar"' not in html
    assert 'id="btn-exportar-html"' not in html


def test_html_marketing_texto_do_banco_nao_escapa_do_mkt_data_json():
    """Causa de atraso vem do atendimento (texto livre). Dentro de um
    `<script>`, a sequência `</script` fecha a tag onde quer que apareça —
    inclusive dentro de uma string JSON."""
    dados = dict(MKT, atrasosData=[
        dict(MKT["atrasosData"][0], causa="</script><script>alert(1)</script>")
    ])
    html = renderizar(montar_relatorio_marketing(dados, "all"))
    assert "<script>alert(1)</script>" not in html
    assert "alert(1)" in html


# ---------------------------------------------------------------------------
# Render — autocontido de verdade
# ---------------------------------------------------------------------------
def test_html_nao_referencia_nenhum_recurso_externo():
    html = renderizar(_geral())
    # Nada de <script src>, <link href>, <img src> ou @import: o arquivo é
    # aberto fora do Portal (anexo de e-mail, pendrive), possivelmente offline.
    # (URL em comentário/licença dentro do bundle do Chart.js não busca nada —
    # por isso a checagem é sobre atributos de markup e `url(...)` de CSS, não
    # sobre a string "https://" solta no arquivo.)
    assert not re.search(r"<script[^>]+\bsrc=", html)
    assert not re.search(r"<link[^>]+\bhref=", html)
    assert not re.search(r"<img[^>]+\bsrc=", html)
    assert "@import" not in html
    assert not re.search(r"url\(\s*['\"]?(https?:)?//", html)
    assert not re.search(r"""\b(src|href)=['"](https?:)?//""", html)


def test_html_leva_o_chartjs_embutido_e_instancia_os_graficos():
    html = renderizar(_geral())
    assert "Chart.js" in html                      # bundle inline
    assert 'id="dados-graficos"' in html
    assert "new window.Chart(canvas, g.config)" in html
    for g in _geral()["charts"]:
        assert f'id="grafico-{g["id"]}"' in html


def test_html_tem_css_de_impressao_para_virar_pdf():
    assert "@media print" in renderizar(_geral())


def test_texto_do_banco_nao_escapa_do_json_nem_do_html():
    """Assunto/comentário vêm do usuário. Num `<script>`, a sequência `</script`
    fecha a tag onde quer que apareça — inclusive dentro de uma string JSON."""
    graficos = dict(GRAFICOS, por_setor=[{"setor": "</script><script>alert(1)</script>", "total": 1}])
    avaliacoes = [dict(AVALIACOES[0], comentario="<img src=x onerror=alert(1)>")]
    html = renderizar(
        montar_relatorio_geral(
            escopo="TI", periodo="2026-07", kpis=KPIS, graficos=graficos, avaliacoes=avaliacoes
        )
    )
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x onerror" not in html
    # O dado continua lá, só que escapado (< no JSON, &lt; no HTML).
    assert "alert(1)" in html


def test_nome_do_arquivo_carrega_escopo_e_periodo():
    nome = nome_arquivo("Marketing", "JUL/26")
    assert nome.startswith("indicadores_marketing-jul-26_")
    assert nome.endswith(".html")

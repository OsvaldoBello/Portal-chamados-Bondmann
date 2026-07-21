/* Painel Admin — gráficos Chart.js (Fase 5). CSP-safe: lê os dados de um
 * <script type="application/json"> inerte (sem eval/inline). */
(function () {
  "use strict";
  if (!window.Chart) return;
  var el = document.getElementById("chart-data");
  if (!el) return;
  var d;
  try { d = JSON.parse(el.textContent); } catch (e) { return; }

  var NAVY = "#2E466F", GREEN = "#7FA53D";
  var STATUS_COR = { NOVO: "#2563EB", EM_ATENDIMENTO: "#6366F1", AGUARDANDO: "#F59E0B", RESOLVIDO: "#16A34A" };

  function bar(id, labels, data, cor, horizontal) {
    var c = document.getElementById(id);
    if (!c) return;
    new window.Chart(c, {
      type: "bar",
      data: { labels: labels, datasets: [{ data: data, backgroundColor: cor, borderRadius: 4 }] },
      options: {
        indexAxis: horizontal ? "y" : "x",
        plugins: { legend: { display: false } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } },
      },
    });
  }

  // Status
  var st = d.por_status || {};
  var stLabels = Object.keys(st);
  bar("chart-status", stLabels.map(function (k) { return k.replace("_", " "); }),
      stLabels.map(function (k) { return st[k]; }),
      stLabels.map(function (k) { return STATUS_COR[k] || NAVY; }));

  // CSAT 1..5 — clicar numa barra abre o modal com os chamados daquela nota
  // (Seção "Distribuição do CSAT"). Sem Alpine: mesmo padrão vanilla JS do
  // resto do painel admin.
  var cs = d.csat || {};
  var csatCanvas = document.getElementById("chart-csat");
  if (csatCanvas) {
    new window.Chart(csatCanvas, {
      type: "bar",
      data: {
        labels: ["1★", "2★", "3★", "4★", "5★"],
        datasets: [{ data: [1, 2, 3, 4, 5].map(function (n) { return cs[n] || 0; }), backgroundColor: GREEN, borderRadius: 4 }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: { x: { grid: { display: false } }, y: { beginAtZero: true, ticks: { precision: 0 } } },
        onClick: function (evt, elements) {
          if (!elements.length) return;
          abrirModalCsat(elements[0].index + 1);
        },
        onHover: function (evt, elements) {
          evt.native.target.style.cursor = elements.length ? "pointer" : "default";
        },
      },
    });
  }

  // ---- Modal "Chamados desta nota" -------------------------------------
  var modal = document.getElementById("csat-modal");
  var modalTitulo = document.getElementById("csat-modal-titulo");
  var modalBusca = document.getElementById("csat-modal-busca");
  var notaAtual = null;

  function carregarModalCsat() {
    if (!modal || !window.htmx || notaAtual === null) return;
    var params = new URLSearchParams({
      nota: String(notaAtual),
      periodo: modal.dataset.periodo || "",
      departamento: modal.dataset.departamento || "",
      busca: (modalBusca && modalBusca.value) || "",
    });
    window.htmx.ajax("GET", "/admin/indicadores/avaliacoes?" + params.toString(), {
      target: "#csat-modal-lista", swap: "innerHTML",
    });
  }

  window.abrirModalCsat = function (nota) {
    if (!modal) return;
    notaAtual = nota;
    if (modalTitulo) modalTitulo.textContent = "Chamados avaliados com " + nota + " estrela" + (nota > 1 ? "s" : "");
    if (modalBusca) modalBusca.value = "";
    // classList, não `.hidden`/atributo: o modal precisa de `flex` estático pra
    // centralizar o card, e o `[hidden]` do Preflight tem especificidade zero
    // (:where()) — perde pra `.flex` e o modal nunca se esconde de fato. Como
    // classe, `.hidden` entra depois de `.flex` no CSS compilado e vence.
    modal.classList.remove("hidden");
    // Trava o scroll da página por trás: o modal já é `fixed inset-0` (sempre
    // centralizado na viewport), mas sem isso a página de baixo continua
    // rolando livremente enquanto ele está aberto — rolar por engano faz
    // parecer que o modal "não está centralizado"/preciso rolar pra achar.
    // `document.scrollingElement` é o `<html>`, não o `<body>` (documento em
    // standards mode) — travar só o `<body>` não impede o scroll real do
    // mouse/touch/teclado, por isso os dois. Fecha (X, clique fora, Esc)
    // devolve o scroll.
    document.documentElement.classList.add("overflow-hidden");
    document.body.classList.add("overflow-hidden");
    carregarModalCsat();
  };

  function fecharModalCsat() {
    if (modal) modal.classList.add("hidden");
    document.documentElement.classList.remove("overflow-hidden");
    document.body.classList.remove("overflow-hidden");
    notaAtual = null;
  }

  var fecharBtn = document.getElementById("csat-modal-fechar");
  if (fecharBtn) fecharBtn.addEventListener("click", fecharModalCsat);
  if (modal) {
    modal.addEventListener("click", function (evt) {
      if (evt.target === modal) fecharModalCsat();
    });
  }
  document.addEventListener("keydown", function (evt) {
    if (evt.key === "Escape" && modal && !modal.classList.contains("hidden")) fecharModalCsat();
  });
  if (modalBusca) {
    var buscaTimer = null;
    modalBusca.addEventListener("input", function () {
      clearTimeout(buscaTimer);
      buscaTimer = setTimeout(carregarModalCsat, 350);
    });
  }

  // Departamento
  var dep = d.por_departamento || [];
  bar("chart-departamento", dep.map(function (x) { return x.departamento; }),
      dep.map(function (x) { return x.total; }), NAVY, true);

  // Setor
  var set = d.por_setor || [];
  bar("chart-setor", set.map(function (x) { return x.setor; }),
      set.map(function (x) { return x.total; }), NAVY, true);

  // Produtividade
  var pr = d.produtividade || [];
  bar("chart-produtividade", pr.map(function (x) { return x.operador; }),
      pr.map(function (x) { return x.resolvidos; }), GREEN, true);
})();

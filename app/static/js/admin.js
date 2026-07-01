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

  // CSAT 1..5
  var cs = d.csat || {};
  bar("chart-csat", ["1★", "2★", "3★", "4★", "5★"],
      [1, 2, 3, 4, 5].map(function (n) { return cs[n] || 0; }), GREEN);

  // Departamento
  var dep = d.por_departamento || [];
  bar("chart-departamento", dep.map(function (x) { return x.departamento; }),
      dep.map(function (x) { return x.total; }), NAVY, true);

  // Produtividade
  var pr = d.produtividade || [];
  bar("chart-produtividade", pr.map(function (x) { return x.operador; }),
      pr.map(function (x) { return x.resolvidos; }), GREEN, true);
})();

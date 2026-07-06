/* Abertura de chamado (Portal) — ajustes específicos do departamento Marketing.
 * Quando o departamento de destino é o Marketing:
 *   - exibe o aviso de prazo (mínimo de 48h);
 *   - troca o texto de ajuda (placeholder) da descrição pelo texto do Marketing.
 * CSP-safe: JS externo, sem eval/inline. Padrão vanilla do projeto (ver shell.js).
 */
(function () {
  "use strict";

  var form = document.querySelector("form[data-marketing-dep]");
  if (!form) return;
  var marketingId = form.getAttribute("data-marketing-dep") || "";
  var depSelect = document.getElementById("departamento-select");
  var aviso = document.getElementById("marketing-aviso");
  var descricao = document.getElementById("descricao-input");
  var campoPrioridade = document.getElementById("campo-prioridade");
  var campoData = document.getElementById("campo-data-entrega");

  function ehMarketing() {
    return marketingId !== "" && depSelect && depSelect.value === marketingId;
  }

  function aplicar() {
    var marketing = ehMarketing();
    // Controla a visibilidade via style.display (CSSOM, permitido pela CSP): o
    // atributo `hidden` sozinho é sobreposto pela classe .block do Tailwind.
    if (aviso) aviso.style.display = marketing ? "block" : "none";
    if (descricao) {
      var padrao = descricao.getAttribute("data-placeholder-padrao") || "";
      var mkt = descricao.getAttribute("data-placeholder-marketing") || "";
      descricao.setAttribute("placeholder", marketing ? mkt : padrao);
    }
    // Marketing → data de entrega (por demanda); demais → prioridade.
    if (campoPrioridade) campoPrioridade.style.display = marketing ? "none" : "block";
    if (campoData) campoData.style.display = marketing ? "block" : "none";
  }

  if (depSelect) depSelect.addEventListener("change", aplicar);
  aplicar(); // estado inicial (ex.: re-render de erro com Marketing já selecionado)
})();

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
  var campoVolume = document.getElementById("campo-volume");
  var semPrazoCheckbox = document.getElementById("sem-prazo-checkbox");
  var dataEntregaInput = document.getElementById("data-entrega-input");

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
    if (campoVolume) campoVolume.style.display = marketing ? "block" : "none";
  }

  // "Sem data limite": desabilita (e limpa) o campo de data enquanto marcado,
  // pra não submeter um valor de data junto com a demanda sem prazo (0040).
  function aplicarSemPrazo() {
    if (!semPrazoCheckbox || !dataEntregaInput) return;
    var semPrazo = semPrazoCheckbox.checked;
    dataEntregaInput.disabled = semPrazo;
    if (semPrazo) dataEntregaInput.value = "";
  }

  if (depSelect) depSelect.addEventListener("change", aplicar);
  if (semPrazoCheckbox) semPrazoCheckbox.addEventListener("change", aplicarSemPrazo);
  aplicar(); // estado inicial (ex.: re-render de erro com Marketing já selecionado)
  aplicarSemPrazo();

  // Categoria "Outros": ao trocar a categoria, o HTMX recarrega as <option>s de
  // subcategoria (fetch em /portal/chamados/subcategorias). Quando a categoria
  // escolhida é "Outros", o catálogo (migration 0030) garante que ela só tem UMA
  // subcategoria, também "Outros" — em vez de deixar o usuário selecionar essa
  // única opção manualmente, pré-seleciona ela assim que o swap termina.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var alvo = evt.detail && evt.detail.target;
    if (!alvo || alvo.id !== "subcategoria-select") return;
    var catSelect = document.getElementById("categoria-select");
    if (!catSelect) return;
    var catOpt = catSelect.options[catSelect.selectedIndex];
    var catNome = catOpt ? (catOpt.textContent || "").trim() : "";
    if (catNome !== "Outros") return;
    for (var i = 0; i < alvo.options.length; i++) {
      if ((alvo.options[i].textContent || "").trim() === "Outros") {
        alvo.value = alvo.options[i].value;
        break;
      }
    }
  });
})();

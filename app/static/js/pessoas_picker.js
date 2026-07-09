/* Seletor de pessoas com busca (Fase 8) — troca o <select>/<select multiple> de
 * "Adicionar em cópia" por um campo de texto: digitar filtra as opções do
 * <select> oculto (fonte de verdade pro form) e a escolha vira chip/valor
 * selecionado. Nenhum dado de domínio novo no JS, só reflete o <select>
 * (Seção 1.3). CSP-safe: sem eval, sem inline handlers — padrão vanilla do
 * projeto (ver shell.js).
 */
(function () {
  "use strict";

  var MARCA_DIACRITICA = new RegExp(
    String.fromCharCode(91, 92, 117, 48, 51, 48, 48, 45, 92, 117, 48, 51, 54, 102, 93),
    "g"
  ); // equivalente a /[̀-ͯ]/ — evita caracteres combinantes crus no arquivo-fonte

  function normalizar(txt) {
    return (txt || "")
      .toString()
      .normalize("NFD")
      .replace(MARCA_DIACRITICA, "")
      .toLowerCase();
  }

  function iniciar(root) {
    var select = root.querySelector("[data-pessoas-select]");
    var input = root.querySelector("[data-pessoas-input]");
    var lista = root.querySelector("[data-pessoas-lista]");
    var chips = root.querySelector("[data-pessoas-chips]");
    if (!select || !input || !lista) return;

    var multi = root.getAttribute("data-pessoas-multi") === "1";
    var submitSeletor = root.getAttribute("data-pessoas-submit");
    var botaoSubmit = submitSeletor ? document.querySelector(submitSeletor) : null;
    var opcoes = Array.prototype.map.call(select.options, function (op) {
      return { op: op, nome: (op.textContent || "").trim() };
    });
    var sugestoesAtuais = [];

    // Um <select> sem `multiple` seleciona a 1ª <option> por padrão quando
    // nenhuma tem `selected` — sem isto, o form nasceria com uma pessoa
    // "escolhida" sem o usuário ter feito nada. `option.selected = false` não
    // basta aqui: num select single a "selectedness" é normalizada pelo
    // browser, que reseleciona a 1ª opção; só `selectedIndex = -1` limpa de
    // fato.
    if (!multi) select.selectedIndex = -1;

    function selecionadas() {
      return opcoes.filter(function (o) { return o.op.selected; });
    }

    function atualizarSubmit() {
      if (botaoSubmit) botaoSubmit.disabled = selecionadas().length === 0;
    }

    function renderChips() {
      if (!chips) return;
      chips.innerHTML = "";
      selecionadas().forEach(function (o) {
        var chip = document.createElement("span");
        chip.className = "inline-flex items-center gap-1.5 rounded-full bg-navy-100 pl-3 pr-1.5 py-1 text-xs font-semibold text-navy";
        chip.appendChild(document.createTextNode(o.nome));
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "w-4 h-4 grid place-items-center rounded-full hover:bg-navy-200";
        btn.setAttribute("aria-label", "Remover " + o.nome);
        btn.textContent = "×";
        btn.addEventListener("click", function () {
          o.op.selected = false;
          renderChips();
          atualizarSubmit();
          input.focus();
        });
        chip.appendChild(btn);
        chips.appendChild(chip);
      });
    }

    function esconderLista() {
      lista.classList.add("hidden");
      lista.innerHTML = "";
      sugestoesAtuais = [];
    }

    function escolher(o) {
      if (!multi) select.selectedIndex = -1;
      o.op.selected = true;
      input.value = multi ? "" : o.nome;
      renderChips();
      atualizarSubmit();
      esconderLista();
      input.focus();
    }

    function mostrarSugestoes() {
      if (!multi) {
        var sel = selecionadas()[0];
        if (sel && input.value !== sel.nome) {
          select.selectedIndex = -1;
          atualizarSubmit();
        }
      }
      var termo = normalizar(input.value);
      lista.innerHTML = "";
      if (!termo) { esconderLista(); return; }
      sugestoesAtuais = opcoes.filter(function (o) {
        return !o.op.selected && normalizar(o.nome).indexOf(termo) !== -1;
      }).slice(0, 8);
      if (!sugestoesAtuais.length) { esconderLista(); return; }
      sugestoesAtuais.forEach(function (o) {
        var li = document.createElement("li");
        li.textContent = o.nome;
        li.className = "px-3.5 py-2 cursor-pointer hover:bg-surface text-ink";
        li.addEventListener("mousedown", function (e) {
          e.preventDefault(); // evita o blur do input antes do click
          escolher(o);
        });
        lista.appendChild(li);
      });
      lista.classList.remove("hidden");
    }

    input.addEventListener("input", mostrarSugestoes);
    input.addEventListener("focus", mostrarSugestoes);
    input.addEventListener("keydown", function (e) {
      if (e.key === "Escape") { esconderLista(); return; }
      if (e.key === "Enter" && sugestoesAtuais.length) {
        e.preventDefault();
        escolher(sugestoesAtuais[0]);
        return;
      }
      if (e.key === "Backspace" && !input.value && multi) {
        var atuais = selecionadas();
        var ultimo = atuais[atuais.length - 1];
        if (ultimo) {
          ultimo.op.selected = false;
          renderChips();
          atualizarSubmit();
        }
      }
    });
    document.addEventListener("click", function (e) {
      if (!root.contains(e.target)) esconderLista();
    });

    renderChips();
    atualizarSubmit();
  }

  document.querySelectorAll("[data-pessoas-picker]").forEach(iniciar);
})();

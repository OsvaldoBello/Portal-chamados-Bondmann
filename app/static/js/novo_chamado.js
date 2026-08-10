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
  var quimicoId = form.getAttribute("data-quimico-dep") || "";
  var camposDinamicos = document.getElementById("campos-dinamicos");
  var campoSubcategoria = document.getElementById("campo-subcategoria");
  var categoriaLabel = document.getElementById("categoria-label");
  var campoAssunto = document.getElementById("campo-assunto");
  var campoDescricao = document.getElementById("campo-descricao");
  var tituloInput = document.querySelector('input[name="titulo"]');
  var depSelect = document.getElementById("departamento-select");
  var aviso = document.getElementById("marketing-aviso");
  var descricao = document.getElementById("descricao-input");
  var campoPrioridade = document.getElementById("campo-prioridade");
  var campoData = document.getElementById("campo-data-entrega");
  var campoVolume = document.getElementById("campo-volume");
  var semPrazoCheckbox = document.getElementById("sem-prazo-checkbox");
  var dataEntregaInput = document.getElementById("data-entrega-input");
  var anexosHint = document.getElementById("anexos-limite-hint");
  var subcategoriaSelect = document.getElementById("subcategoria-select");

  function ehMarketing() {
    return marketingId !== "" && depSelect && depSelect.value === marketingId;
  }

  function ehQuimico() {
    return quimicoId !== "" && depSelect && depSelect.value === quimicoId;
  }

  function aplicar() {
    var marketing = ehMarketing();
    var quimico = ehQuimico();
    // Controla a visibilidade via style.display (CSSOM, permitido pela CSP): o
    // atributo `hidden` sozinho é sobreposto pela classe .block do Tailwind.
    if (aviso) aviso.style.display = marketing ? "block" : "none";
    if (descricao) {
      var padrao = descricao.getAttribute("data-placeholder-padrao") || "";
      var mkt = descricao.getAttribute("data-placeholder-marketing") || "";
      descricao.setAttribute("placeholder", marketing ? mkt : padrao);
    }
    // Marketing → data de entrega (por demanda); demais → prioridade. Químico
    // não pergunta prioridade (esconde e mantém o valor padrão MEDIA do select).
    if (campoPrioridade) campoPrioridade.style.display = (marketing || quimico) ? "none" : "block";
    if (campoData) campoData.style.display = marketing ? "block" : "none";
    if (campoVolume) campoVolume.style.display = marketing ? "block" : "none";
    // Químico → bloco de campos dinâmicos por categoria (carregados via HTMX).
    if (camposDinamicos) camposDinamicos.style.display = quimico ? "block" : "none";
    // Químico → nenhuma categoria do setor tem subcategoria (0049): esconde o
    // campo em vez de deixá-lo parado em "Escolha a categoria primeiro".
    if (campoSubcategoria) campoSubcategoria.style.display = quimico ? "none" : "block";
    // Químico → rótulo "Categoria" vira "Formulários" (são os 3 formulários do
    // setor, não uma categoria genérica).
    if (categoriaLabel) categoriaLabel.textContent = quimico ? "Formulários" : "Categoria";
    // Químico → esconde Assunto/Descrição (o servidor deriva os dois das
    // respostas do formulário dinâmico, app/domain/formularios_quimico.py).
    // Tira o `required` junto: um campo obrigatório escondido via display:none
    // continua bloqueando o envio nativo do formulário (a barra de validação do
    // HTML5 não isenta elementos só por estarem sem `display`).
    if (campoAssunto) campoAssunto.style.display = quimico ? "none" : "block";
    if (tituloInput) tituloInput.required = !quimico;
    if (campoDescricao) campoDescricao.style.display = quimico ? "none" : "block";
    if (descricao) descricao.required = !quimico;
    // Químico aceita anexos maiores (laudos/fotos/vídeos de análise) — troca o
    // aviso de limite exibido pelo mesmo padrão do placeholder do Marketing.
    if (anexosHint) {
      var hintPadrao = anexosHint.getAttribute("data-hint-padrao") || "";
      var hintQuimico = anexosHint.getAttribute("data-hint-quimico") || "";
      anexosHint.innerHTML = quimico ? hintQuimico : hintPadrao;
    }
  }

  // Formulário obrigatório do RH (2026-08-10): mostra o aviso/link de download
  // do bloco oculto (novo_chamado.html) cujo `data-subcategoria` casa com o
  // texto da <option> selecionada — mesmo formato de nome usado no banco
  // (`subcategorias.nome`), sem precisar de mais uma chamada ao servidor.
  function aplicarFormularioObrigatorio() {
    if (!subcategoriaSelect) return;
    var opt = subcategoriaSelect.options[subcategoriaSelect.selectedIndex];
    var nomeSelecionado = opt ? (opt.textContent || "").trim() : "";
    var avisos = document.querySelectorAll(".formulario-rh-aviso");
    for (var i = 0; i < avisos.length; i++) {
      var bate = avisos[i].getAttribute("data-subcategoria") === nomeSelecionado;
      avisos[i].hidden = !bate;
    }
  }

  // "Sem data limite": desabilita (e limpa) o campo de data enquanto marcado,
  // pra não submeter um valor de data junto com a demanda sem prazo (0040).
  function aplicarSemPrazo() {
    if (!semPrazoCheckbox || !dataEntregaInput) return;
    var semPrazo = semPrazoCheckbox.checked;
    dataEntregaInput.disabled = semPrazo;
    if (semPrazo) dataEntregaInput.value = "";
  }

  // O Marketing não atende aos finais de semana: bloqueia sábado/domingo no
  // campo de data (o backend também valida — esta checagem é só UX).
  function validarDataEntrega() {
    if (!dataEntregaInput || !dataEntregaInput.value) return;
    var partes = dataEntregaInput.value.split("-");
    var d = new Date(Number(partes[0]), Number(partes[1]) - 1, Number(partes[2]));
    var diaDaSemana = d.getDay(); // 0 = domingo, 6 = sábado
    if (diaDaSemana === 0 || diaDaSemana === 6) {
      dataEntregaInput.setCustomValidity(
        "O Marketing não atende aos finais de semana — escolha um dia útil (segunda a sexta)."
      );
    } else {
      dataEntregaInput.setCustomValidity("");
    }
    dataEntregaInput.reportValidity();
  }

  if (depSelect) depSelect.addEventListener("change", aplicar);
  if (semPrazoCheckbox) semPrazoCheckbox.addEventListener("change", aplicarSemPrazo);
  if (dataEntregaInput) {
    dataEntregaInput.addEventListener("input", validarDataEntrega);
    dataEntregaInput.addEventListener("change", validarDataEntrega);
  }
  if (subcategoriaSelect) subcategoriaSelect.addEventListener("change", aplicarFormularioObrigatorio);
  aplicar(); // estado inicial (ex.: re-render de erro com Marketing já selecionado)
  aplicarSemPrazo();
  aplicarFormularioObrigatorio(); // estado inicial (re-render de erro com subcategoria já selecionada)

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

  // A recarga das <option>s de subcategoria (cascade da categoria) muda a
  // seleção — reavalia o aviso de formulário obrigatório depois de qualquer
  // swap do select (inclui o pré-select de "Outros" acima).
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var alvo = evt.detail && evt.detail.target;
    if (alvo && alvo.id === "subcategoria-select") aplicarFormularioObrigatorio();
  });
})();

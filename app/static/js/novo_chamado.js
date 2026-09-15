/* Abertura de chamado (Portal) — ajustes por departamento e por layout dinâmico.
 * Marketing: aviso de prazo (48h), placeholder próprio, data de entrega no lugar
 * de prioridade. Químico: rótulo "Formulários", sem subcategoria, anexos maiores.
 * Layout dinâmico (bloco #campos-dinamicos, servido por /portal/chamados/campos):
 * esconde Assunto/Descrição/Prioridade conforme os data-* do wrapper, mostra/
 * esconde campos condicionais (data-visivel-se-*) e sugere o e-mail corporativo
 * (data-sugere-email-de). CSP-safe: JS externo, sem eval/inline (ver shell.js).
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
    // Marketing → data de entrega (por demanda); demais → prioridade (o layout
    // dinâmico do Químico também esconde — ver aplicarLayoutDinamico).
    if (campoData) campoData.style.display = marketing ? "block" : "none";
    if (campoVolume) campoVolume.style.display = marketing ? "block" : "none";
    // Bloco de campos dinâmicos, Assunto/Descrição e Prioridade: decididos pelo
    // layout servido (Químico ou TI "Usuários e Acessos"), não pelo departamento.
    aplicarLayoutDinamico(marketing);
    // Químico → nenhuma categoria do setor tem subcategoria (0049): esconde o
    // campo em vez de deixá-lo parado em "Escolha a categoria primeiro".
    if (campoSubcategoria) campoSubcategoria.style.display = quimico ? "none" : "block";
    // Químico → rótulo "Categoria" vira "Formulários" (são os 3 formulários do
    // setor, não uma categoria genérica).
    if (categoriaLabel) categoriaLabel.textContent = quimico ? "Formulários" : "Categoria";
    // Químico aceita anexos maiores (laudos/fotos/vídeos de análise) — troca o
    // aviso de limite exibido pelo mesmo padrão do placeholder do Marketing.
    if (anexosHint) {
      var hintPadrao = anexosHint.getAttribute("data-hint-padrao") || "";
      var hintQuimico = anexosHint.getAttribute("data-hint-quimico") || "";
      anexosHint.innerHTML = quimico ? hintQuimico : hintPadrao;
    }
  }

  // Wrapper do layout dinâmico atual (null quando a escolha não tem campos).
  function layoutAtual() {
    return camposDinamicos ? camposDinamicos.querySelector("[data-layout]") : null;
  }

  // Sugestão de e-mail corporativo a partir do nome (mesma regra da automação:
  // primeiro nome + "." + último sobrenome, sem acentos, minúsculo).
  function sugerirEmail(nome) {
    var limpo = (nome || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
    var partes = limpo.split(/\s+/).filter(function (p) { return /^[a-z0-9]+$/.test(p); });
    if (!partes.length) return "";
    var local = partes.length > 1 ? partes[0] + "." + partes[partes.length - 1] : partes[0];
    return local + "@bondmann.com.br";
  }

  // Campos condicionais (data-visivel-se-*): mostra só quando o campo
  // controlador (um select do mesmo layout) está num dos valores listados.
  // Inputs escondidos ficam `disabled` — não são submetidos e não travam a
  // validação nativa (um `required` invisível bloquearia o envio).
  function aplicarCondicionais(wrapper) {
    var blocos = wrapper.querySelectorAll(".campo-dinamico[data-visivel-se-campo]");
    for (var i = 0; i < blocos.length; i++) {
      var bloco = blocos[i];
      var controlador = wrapper.querySelector('[name="campo__' + bloco.getAttribute("data-visivel-se-campo") + '"]');
      var valores = (bloco.getAttribute("data-visivel-se-valores") || "").split("||");
      var visivel = !!controlador && valores.indexOf(controlador.value) >= 0;
      bloco.style.display = visivel ? "block" : "none";
      var inputs = bloco.querySelectorAll("input, select, textarea");
      for (var j = 0; j < inputs.length; j++) inputs[j].disabled = !visivel;
    }
  }

  // Aplica o layout dinâmico servido em #campos-dinamicos: visibilidade do
  // bloco, de Assunto/Descrição (derivados no servidor quando escondidos —
  // tira o `required` junto, senão o navegador bloqueia o envio de um campo
  // obrigatório invisível) e de Prioridade; campos condicionais; e-mail sugerido.
  function aplicarLayoutDinamico(marketing) {
    if (typeof marketing === "undefined") marketing = ehMarketing();
    var wrapper = layoutAtual();
    var temLayout = !!wrapper;
    var ocultaAssunto = temLayout && wrapper.getAttribute("data-oculta-assunto") === "1";
    var ocultaPrioridade = temLayout && wrapper.getAttribute("data-oculta-prioridade") === "1";
    if (camposDinamicos) camposDinamicos.style.display = temLayout ? "block" : "none";
    if (campoAssunto) campoAssunto.style.display = ocultaAssunto ? "none" : "block";
    if (tituloInput) tituloInput.required = !ocultaAssunto;
    if (campoDescricao) campoDescricao.style.display = ocultaAssunto ? "none" : "block";
    if (descricao) descricao.required = !ocultaAssunto;
    if (campoPrioridade) campoPrioridade.style.display = (marketing || ocultaPrioridade) ? "none" : "block";
    if (!wrapper) return;
    aplicarCondicionais(wrapper);
    if (wrapper.getAttribute("data-ligado") === "1") return; // listeners já instalados neste swap
    wrapper.setAttribute("data-ligado", "1");
    wrapper.addEventListener("change", function (evt) {
      if (evt.target && evt.target.tagName === "SELECT") aplicarCondicionais(wrapper);
    });
    var fonteEmail = wrapper.getAttribute("data-sugere-email-de");
    var emailInput = wrapper.querySelector('[name="campo__email"]');
    var fonteInput = fonteEmail ? wrapper.querySelector('[name="campo__' + fonteEmail + '"]') : null;
    if (emailInput && fonteInput) {
      if (!emailInput.value) emailInput.setAttribute("data-auto", "1");
      fonteInput.addEventListener("input", function () {
        if (emailInput.getAttribute("data-auto") !== "1") return; // o usuário editou o e-mail à mão
        emailInput.value = sugerirEmail(fonteInput.value);
      });
      emailInput.addEventListener("input", function () {
        emailInput.setAttribute("data-auto", emailInput.value ? "0" : "1");
      });
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
  // swap do select (inclui o pré-select de "Outros" acima). O swap do bloco de
  // campos dinâmicos (troca de categoria/subcategoria) reaplica o layout.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    var alvo = evt.detail && evt.detail.target;
    if (alvo && alvo.id === "subcategoria-select") aplicarFormularioObrigatorio();
    if (alvo && alvo.id === "campos-dinamicos") aplicarLayoutDinamico();
  });
})();

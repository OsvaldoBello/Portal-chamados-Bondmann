// Avaliação do chamado (CSAT): notas de 4 estrelas ou menos exigem um
// comentário de pelo menos 50 caracteres (validado de verdade no servidor,
// app/repositories/chamados.py::validar_comentario_avaliacao — isto é só UX).
//
// Delegação de evento em `document`: o fragmento #avaliacao é trocado via
// HTMX (outerHTML) após o POST, então um listener preso ao elemento antigo
// sumiria junto. Delegar no documento sobrevive a qualquer swap.
(function () {
  var COMENTARIO_MIN_CHARS = 50;
  var NOTA_COMENTARIO_OBRIGATORIO = 4;

  function atualizar(radio) {
    var form = radio.closest("form");
    if (!form) return;
    var comentario = form.querySelector("#avaliacao-comentario");
    if (!comentario) return;
    var aviso = form.querySelector("#comentario-aviso-obrigatorio");
    var opcional = form.querySelector("#comentario-opcional-tag");
    var nota = parseInt(radio.value, 10);
    var obrigatorio = nota >= 1 && nota <= NOTA_COMENTARIO_OBRIGATORIO;
    comentario.required = obrigatorio;
    comentario.minLength = obrigatorio ? COMENTARIO_MIN_CHARS : 0;
    if (aviso) aviso.hidden = !obrigatorio;
    if (opcional) opcional.hidden = obrigatorio;
  }

  document.addEventListener("change", function (ev) {
    if (ev.target.matches && ev.target.matches('#avaliacao input[name="nota"]')) {
      atualizar(ev.target);
    }
  });
})();

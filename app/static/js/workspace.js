/* Workspace do operador (Fase 4).
 * - Kanban: drag-and-drop com Sortable.js; ao soltar num status, persiste via
 *   POST (CSRF do cookie) e recarrega para refletir contadores/SLA.
 * - Composer de atendimento: toggle "Nota interna" pinta o fundo de amarelo
 *   (o valor real de is_interna é decidido/validado no servidor).
 * CSP-safe: sem eval/inline.
 */
(function () {
  "use strict";

  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  // ---- Kanban DnD ----
  if (window.Sortable) {
    var cols = document.querySelectorAll(".kanban-col");
    cols.forEach(function (col) {
      new window.Sortable(col, {
        group: "chamados",
        animation: 150,
        ghostClass: "opacity-40",
        onEnd: function (evt) {
          var card = evt.item;
          var destino = evt.to.getAttribute("data-status");
          var origem = evt.from.getAttribute("data-status");
          var id = card.getAttribute("data-id");
          if (!id || destino === origem) return;
          var body = new URLSearchParams();
          body.set("novo_status", destino);
          body.set("csrf_token", csrf());
          fetch("/workspace/chamados/" + id + "/status", {
            method: "POST",
            headers: { "X-CSRF-Token": csrf(), "Content-Type": "application/x-www-form-urlencoded" },
            body: body.toString(),
            credentials: "same-origin",
          }).then(function () { window.location.reload(); })
            .catch(function () { window.location.reload(); });
        },
      });
    });
  }

  // ---- Toggle nota interna ----
  var chk = document.getElementById("is-interna");
  var composer = document.getElementById("composer");
  if (chk && composer) {
    var apply = function () {
      composer.classList.toggle("bg-amber-50", chk.checked);
      composer.classList.toggle("ring-amber-300", chk.checked);
    };
    chk.addEventListener("change", apply);
    apply();
  }
})();

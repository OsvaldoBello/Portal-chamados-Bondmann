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
  // forceFallback: usa o arraste por eventos de mouse do próprio Sortable (não o
  // HTML5 Drag nativo), evitando que o <a> do cartão sequestre o gesto. handle no
  // próprio cartão; o clique no link continua abrindo o chamado (o Sortable
  // distingue clique de arraste pela tolerância).
  if (window.Sortable) {
    var cols = document.querySelectorAll(".kanban-col");
    cols.forEach(function (col) {
      new window.Sortable(col, {
        group: "chamados",
        animation: 150,
        ghostClass: "opacity-40",
        forceFallback: true,
        fallbackTolerance: 4,
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
            headers: {
              "X-CSRF-Token": csrf(),
              "X-Kanban-Drag": "1",
              "Content-Type": "application/x-www-form-urlencoded",
            },
            body: body.toString(),
            credentials: "same-origin",
          })
            .then(function (resp) {
              if (!resp.ok) {
                // Ex.: CSRF expirado (403). O servidor não aplicou a mudança.
                throw new Error("http_" + resp.status);
              }
              return resp.json();
            })
            .then(function (data) {
              if (!data.ok) {
                // A mudança não teve efeito no servidor (ex.: alguém já assumiu o
                // chamado). Sem isso, o card "voltava" pra coluna antiga sem
                // nenhuma explicação — parecia que o Kanban tinha travado.
                window.alert(
                  "Não foi possível mover o chamado para \"" + destino +
                  "\". Ele pode já ter sido assumido por outra pessoa. A tela vai recarregar."
                );
              }
              window.location.reload();
            })
            .catch(function () {
              window.alert("Falha ao salvar a mudança de status. A tela vai recarregar.");
              window.location.reload();
            });
        },
      });
    });
  }

  // ---- Exclusão rápida (lixeira do cartão do Kanban) ----
  document.querySelectorAll(".kanban-delete-btn").forEach(function (btn) {
    btn.addEventListener("click", function (evt) {
      evt.preventDefault();
      evt.stopPropagation();
      var id = btn.getAttribute("data-id");
      var codigo = btn.getAttribute("data-codigo") || "este chamado";
      if (!id) return;
      if (!window.confirm("Excluir o chamado " + codigo + "? Esta ação é permanente e apaga toda a conversa.")) {
        return;
      }
      var body = new URLSearchParams();
      body.set("origem", "kanban");
      body.set("csrf_token", csrf());
      fetch("/workspace/chamados/" + id + "/excluir", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf(), "Content-Type": "application/x-www-form-urlencoded" },
        body: body.toString(),
        credentials: "same-origin",
      }).then(function () { window.location.reload(); })
        .catch(function () { window.location.reload(); });
    });
  });

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

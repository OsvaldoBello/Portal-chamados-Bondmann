/* Shell responsivo (CSP-safe, sem eval/inline — Seção 3.8).
 * - Sidebar off-canvas no mobile: hambúrguer abre; overlay/Esc/clique em link fecha.
 * - Dropdown de usuário na topbar: abre/fecha; clique fora e Esc fecham.
 * Segue o padrão vanilla-JS do projeto (ver workspace.js).
 */
(function () {
  "use strict";

  var sidebar = document.getElementById("sidebar");
  var overlay = document.getElementById("sidebar-overlay");

  function openSidebar() {
    if (!sidebar) return;
    sidebar.classList.remove("-translate-x-full");
    if (overlay) overlay.classList.remove("hidden");
  }
  function closeSidebar() {
    if (!sidebar) return;
    sidebar.classList.add("-translate-x-full");
    if (overlay) overlay.classList.add("hidden");
  }

  document.querySelectorAll("[data-sidebar-open]").forEach(function (b) {
    b.addEventListener("click", openSidebar);
  });
  document.querySelectorAll("[data-sidebar-close]").forEach(function (b) {
    b.addEventListener("click", closeSidebar);
  });
  if (overlay) overlay.addEventListener("click", closeSidebar);
  // Fecha o drawer ao navegar (mobile).
  if (sidebar) {
    sidebar.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", function () {
        if (window.matchMedia("(max-width: 767px)").matches) closeSidebar();
      });
    });
  }

  // ---- Dropdowns (topbar) ----
  function closeMenus() {
    document.querySelectorAll("[data-menu]").forEach(function (m) {
      m.classList.add("hidden");
    });
  }
  document.querySelectorAll("[data-menu-toggle]").forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var id = btn.getAttribute("data-menu-toggle");
      var menu = document.querySelector('[data-menu="' + id + '"]');
      if (!menu) return;
      var willOpen = menu.classList.contains("hidden");
      closeMenus();
      if (willOpen) menu.classList.remove("hidden");
    });
  });
  document.addEventListener("click", closeMenus);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeSidebar(); closeMenus(); }
  });

  // ---- Auto-submit: selects/inputs com [data-autosubmit] enviam o form ao
  // mudar (dispensa botão "OK" — selecionar/preencher já aplica a ação;
  // usado também pelos filtros de data do Kanban). CSP-safe, sem inline.
  document.querySelectorAll("select[data-autosubmit], input[data-autosubmit]").forEach(function (sel) {
    sel.addEventListener("change", function () {
      if (sel.form) {
        if (sel.form.requestSubmit) sel.form.requestSubmit();
        else sel.form.submit();
      }
    });
  });

  // ---- Barra de progresso de SLA: largura via CSSOM (permitido pela CSP;
  //      style="" inline seria bloqueado por style-src 'self'). ----
  document.querySelectorAll("[data-sla-pct]").forEach(function (el) {
    var p = parseInt(el.getAttribute("data-sla-pct"), 10);
    if (isNaN(p)) p = 0;
    el.style.width = Math.max(0, Math.min(100, p)) + "%";
  });
})();

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

  // ---- Lightbox de imagem: [data-lightbox="url"] (ex.: miniatura de avatar
  // em admin/usuarios.html) abre a imagem ampliada e centralizada no overlay
  // compartilhado `#lightbox` (workspace_base.html). Clique fora (no fundo,
  // não na própria imagem) ou Esc fecha. Mesmo padrão do csat-modal
  // (admin.js): `.hidden`/`.flex` via classList, nunca o atributo `hidden`
  // (Preflight tem especificidade zero nele — perderia pra `.flex` estático e
  // o overlay nunca se escondia de fato). ----
  var lightbox = document.getElementById("lightbox");
  var lightboxImg = document.getElementById("lightbox-img");
  function fecharLightbox() {
    if (!lightbox) return;
    lightbox.classList.add("hidden");
    document.documentElement.classList.remove("overflow-hidden");
    document.body.classList.remove("overflow-hidden");
  }
  if (lightbox && lightboxImg) {
    document.querySelectorAll("[data-lightbox]").forEach(function (el) {
      el.addEventListener("click", function (e) {
        var url = el.getAttribute("data-lightbox");
        if (!url) return;
        e.preventDefault();
        lightboxImg.src = url;
        lightbox.classList.remove("hidden");
        document.documentElement.classList.add("overflow-hidden");
        document.body.classList.add("overflow-hidden");
      });
    });
    lightbox.addEventListener("click", function (e) {
      if (e.target === lightbox) fecharLightbox();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !lightbox.classList.contains("hidden")) fecharLightbox();
    });
  }

  // ---- Barra de progresso de SLA: largura via CSSOM (permitido pela CSP;
  //      style="" inline seria bloqueado por style-src 'self'). ----
  document.querySelectorAll("[data-sla-pct]").forEach(function (el) {
    var p = parseInt(el.getAttribute("data-sla-pct"), 10);
    if (isNaN(p)) p = 0;
    el.style.width = Math.max(0, Math.min(100, p)) + "%";
  });

  // ---- Anexos: <input type="file" multiple> nativo troca a seleção inteira
  // toda vez que o picker reabre — só acumula se o usuário marcar vários
  // arquivos num único diálogo. Aqui a seleção anterior é preservada e
  // mesclada com a nova via DataTransfer, então dá pra anexar um arquivo por
  // vez (mesmo padrão usado nos 3 forms com anexo: abertura de chamado, chat
  // do Portal e do Workspace). Lista de chips abaixo do input mostra o que já
  // foi selecionado, com botão pra remover individualmente.
  document.querySelectorAll('input[type="file"][multiple]').forEach(function (input) {
    var acumulado = [];
    var lista = document.createElement("div");
    lista.className = "flex flex-wrap gap-1.5 mt-1.5";
    lista.setAttribute("data-anexos-lista", "");
    input.insertAdjacentElement("afterend", lista);

    function chave(f) {
      return f.name + ":" + f.size + ":" + f.lastModified;
    }

    function sincronizarInput() {
      var dt = new DataTransfer();
      acumulado.forEach(function (f) { dt.items.add(f); });
      input.files = dt.files;
    }

    function renderizar() {
      lista.innerHTML = "";
      acumulado.forEach(function (f, i) {
        var chip = document.createElement("span");
        chip.className = "inline-flex items-center gap-1 rounded-md bg-surface2 px-2 py-0.5 text-[11px] font-medium text-navy";
        var nome = document.createElement("span");
        nome.textContent = f.name;
        chip.appendChild(nome);
        var btn = document.createElement("button");
        btn.type = "button";
        btn.className = "text-muted hover:text-sla-danger font-bold leading-none";
        btn.setAttribute("aria-label", "Remover " + f.name);
        btn.textContent = "×";
        btn.addEventListener("click", function () {
          acumulado.splice(i, 1);
          sincronizarInput();
          renderizar();
        });
        chip.appendChild(btn);
        lista.appendChild(chip);
      });
    }

    input.addEventListener("change", function () {
      var chaves = acumulado.map(chave);
      Array.prototype.forEach.call(input.files, function (f) {
        if (chaves.indexOf(chave(f)) === -1) {
          acumulado.push(f);
          chaves.push(chave(f));
        }
      });
      sincronizarInput();
      renderizar();
    });
  });
})();

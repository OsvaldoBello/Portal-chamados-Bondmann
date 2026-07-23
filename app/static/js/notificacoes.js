/* Sino de notificações em tempo real (Fase 4, Seção 6.1).
 *
 * Progressive enhancement, CSP-safe (sem eval/inline): busca a config de
 * Realtime em /realtime/config (JWT do próprio usuário, lido do cookie no
 * servidor) e, se o supabase-js estiver carregado, assina o Realtime de
 * `chamados` e `mensagens`. A RLS aplica na entrega — o usuário só recebe
 * eventos do seu escopo (funcionário: os seus; staff: o setor; TI: tudo).
 *
 * A bolinha vermelha reflete se HÁ chamado NOVO (ou resolvido aguardando
 * avaliação) na lista de /notificacoes — não qualquer item pendente. Um
 * chamado antigo que só ficou com SLA estourado enquanto já em atendimento
 * NÃO conta (2026-07-23: SLA estourado não deve manter o sino "apitando"
 * nem a bolinha vermelha acesa — isso é reservado pra CHAMADOS NOVOS de
 * verdade; ver o marcador `data-notif-novo` em `_notificacoes.html`). A
 * bolinha não é só um "toque" de evento Realtime que nunca se apaga: é
 * recalculada (a) no carregamento da página; (b) toda vez que a lista é
 * recarregada (clique no sino ou refresh disparado por um evento Realtime).
 * Assim, ao ler/resolver todos os chamados novos, a bolinha some sozinha na
 * próxima recarga da lista, sem depender de um clique "apagar aviso".
 *
 * Sem Realtime (config ausente/off), o sino segue funcionando por clique
 * (carrega a lista sob demanda — comportamento base do shell).
 */
(function () {
  "use strict";

  var bell = document.querySelector('[data-menu-toggle="notif"]');
  if (!bell || !window.fetch) return;
  var dot = document.querySelector("[data-notif-dot]");
  var list = document.getElementById("notif-list");

  // Só os itens marcados `data-notif-novo` (chamado NOVO ou resolvido
  // aguardando avaliação — ver _notificacoes.html) acendem a bolinha. Um
  // chamado com SLA estourado mas já em atendimento aparece na lista, porém
  // sem esse marcador, então não conta aqui.
  function atualizarDot(html) {
    if (!dot) return;
    var aceso = /data-notif-novo="1"/.test(html);
    dot.classList.toggle("hidden", !aceso);
    if (!aceso) dot.classList.remove("notif-dot-ping");
  }

  function recarregarLista() {
    fetch("/notificacoes", { credentials: "same-origin", headers: { Accept: "text/html" } })
      .then(function (r) { return r && r.ok ? r.text() : null; })
      .then(function (html) {
        if (html == null) return;
        atualizarDot(html);
        if (list) list.innerHTML = html;
      })
      .catch(function () { /* silencioso: mantém o estado atual do sino */ });
  }

  // Estado inicial ao carregar a página — antes disso a bolinha fica oculta
  // (classe `hidden` por padrão em _sino.html), nunca "sempre vermelha".
  recarregarLista();

  // Se o dropdown está aberto quando a lista é recarregada (clique ou evento
  // Realtime), o próprio HTMX troca o conteúdo (hx-trigger="click once"); aqui
  // só garantimos que a bolinha reflita o resultado desse swap também.
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    if (evt.target && evt.target.id === "notif-list") {
      atualizarDot(evt.target.innerHTML);
    }
  });

  if (!window.supabase || !window.fetch) return;

  var lastPing = 0;
  function sinalizar() {
    var now = Date.now();
    // Debounce: rajadas de eventos (ex.: várias linhas) tocam o sino uma vez.
    if (now - lastPing < 800) return;
    lastPing = now;

    bell.classList.remove("notif-ring");
    void bell.offsetWidth; // reflow: reinicia a animação
    bell.classList.add("notif-ring");
    if (dot) dot.classList.add("notif-dot-ping");

    // Recarrega a lista (e a bolinha) com o novo estado — o dropdown, se
    // aberto, é atualizado pelo HTMX via afterSwap acima.
    var menu = document.querySelector('[data-menu="notif"]');
    if (menu && !menu.classList.contains("hidden") && list && window.htmx) {
      window.htmx.ajax("GET", "/notificacoes", { target: "#notif-list", swap: "innerHTML" });
    } else {
      recarregarLista();
    }
  }

  // Ao abrir o sino, para o halo pulsante (o HTMX cuida de trazer a lista
  // atual — "click once" — e o afterSwap acima recalcula a bolinha).
  bell.addEventListener("click", function () {
    if (dot) dot.classList.remove("notif-dot-ping");
  });

  fetch("/realtime/config", { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then(function (r) { return r && r.ok ? r.json() : null; })
    .then(function (cfg) {
      if (!cfg || !cfg.url || !cfg.key || !cfg.token) return;
      var client = window.supabase.createClient(cfg.url, cfg.key, {
        auth: { persistSession: false, autoRefreshToken: false },
        global: { headers: { Authorization: "Bearer " + cfg.token } },
      });
      if (client.realtime && client.realtime.setAuth) client.realtime.setAuth(cfg.token);

      // event: "INSERT" (não "*"): só chamado NOVO de verdade toca o sino.
      // Um UPDATE em `chamados` (status, prioridade recalculada por SLA,
      // atribuição, resolução...) não deve reacender o sino — 2026-07-23,
      // esse era o gatilho do sino "apitando" toda vez que o SLA estourava.
      var channel = client
        .channel("notif-" + (cfg.uid || "user"))
        .on("postgres_changes", { event: "INSERT", schema: "public", table: "chamados" }, sinalizar)
        .on("postgres_changes", { event: "INSERT", schema: "public", table: "mensagens" }, sinalizar)
        .subscribe();

      window.addEventListener("beforeunload", function () {
        try { client.removeChannel(channel); } catch (e) {}
      });
    })
    .catch(function () { /* silencioso: degrada para o sino por clique */ });
})();

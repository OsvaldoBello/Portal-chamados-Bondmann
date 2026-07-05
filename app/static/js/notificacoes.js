/* Sino de notificações em tempo real (Fase 4, Seção 6.1).
 *
 * Progressive enhancement, CSP-safe (sem eval/inline): busca a config de
 * Realtime em /realtime/config (JWT do próprio usuário, lido do cookie no
 * servidor) e, se o supabase-js estiver carregado, assina o Realtime de
 * `chamados` e `mensagens`. A RLS aplica na entrega — o usuário só recebe
 * eventos do seu escopo (funcionário: os seus; staff: o setor; TI: tudo).
 *
 * A cada mudança significativa (novo chamado, troca de status/prioridade,
 * atribuição, nova mensagem/resposta), acende o ponto do sino, dá um "toque"
 * animado e, se o painel estiver aberto, recarrega a lista via HTMX.
 *
 * Sem Realtime (config ausente/off), o sino segue funcionando por clique
 * (carrega a lista sob demanda — comportamento base do shell).
 */
(function () {
  "use strict";

  var bell = document.querySelector('[data-menu-toggle="notif"]');
  if (!bell || !window.supabase || !window.fetch) return;
  var dot = document.querySelector("[data-notif-dot]");

  var lastPing = 0;
  function sinalizar() {
    var now = Date.now();
    // Debounce: rajadas de eventos (ex.: várias linhas) tocam o sino uma vez.
    if (now - lastPing < 800) return;
    lastPing = now;

    if (dot) {
      dot.classList.remove("hidden");
      dot.classList.add("notif-dot-ping");
    }
    bell.classList.remove("notif-ring");
    void bell.offsetWidth; // reflow: reinicia a animação
    bell.classList.add("notif-ring");

    // Se o dropdown está aberto, recarrega a lista já com o novo estado.
    var menu = document.querySelector('[data-menu="notif"]');
    var list = document.getElementById("notif-list");
    if (menu && !menu.classList.contains("hidden") && list && window.htmx) {
      window.htmx.ajax("GET", "/notificacoes", { target: "#notif-list", swap: "innerHTML" });
    }
  }

  // Ao abrir o sino, o usuário "viu" — para o halo pulsante.
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

      var channel = client
        .channel("notif-" + (cfg.uid || "user"))
        .on("postgres_changes", { event: "*", schema: "public", table: "chamados" }, sinalizar)
        .on("postgres_changes", { event: "INSERT", schema: "public", table: "mensagens" }, sinalizar)
        .subscribe();

      window.addEventListener("beforeunload", function () {
        try { client.removeChannel(channel); } catch (e) {}
      });
    })
    .catch(function () { /* silencioso: degrada para o sino por clique */ });
})();

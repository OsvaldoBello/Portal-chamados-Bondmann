/* Chat em tempo real do chamado (Seção 6.1).
 *
 * Progressive enhancement, CSP-safe (sem eval/inline): lê a config de
 * data-atributos de #chat-root e, se o supabase-js estiver carregado, assina
 * o Realtime de `mensagens` filtrando por chamado_id. A cada nova linha,
 * dispara o evento `chat-nova` no <body> — o container HTMX recarrega o
 * fragmento da conversa (renderizado no servidor, RLS aplicada).
 *
 * Sem Realtime (ou sem config), o polling HTMX de 10s do container mantém a
 * conversa atualizada. A escrita é sempre pelo FastAPI (POST), nunca aqui.
 */
(function () {
  "use strict";
  var root = document.getElementById("chat-root");
  if (!root || !window.supabase) return;

  var url = root.getAttribute("data-supabase-url");
  var key = root.getAttribute("data-anon-key");
  var token = root.getAttribute("data-access-token");
  var chamadoId = root.getAttribute("data-chamado-id");
  if (!url || !key || !token || !chamadoId) return;

  var client = window.supabase.createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
    global: { headers: { Authorization: "Bearer " + token } },
  });
  // Autentica o socket do Realtime com o JWT do usuário (RLS na entrega).
  if (client.realtime && client.realtime.setAuth) client.realtime.setAuth(token);

  var channel = client
    .channel("chamado-" + chamadoId)
    .on(
      "postgres_changes",
      {
        event: "INSERT",
        schema: "public",
        table: "mensagens",
        filter: "chamado_id=eq." + chamadoId,
      },
      function () {
        document.body.dispatchEvent(new CustomEvent("chat-nova"));
      }
    )
    .subscribe();

  window.addEventListener("beforeunload", function () {
    try { client.removeChannel(channel); } catch (e) {}
  });
})();

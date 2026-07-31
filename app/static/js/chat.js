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

  // Anexos usam signed URL regenerada a cada render do fragmento (decisão C2
  // do plano mestre — proibido cachear a URL assinada, então isso não muda).
  // O que quebrava a imagem era o `hx-swap="innerHTML"` do #chat-mensagens
  // trocando todo o HTML (inclusive imagens já carregadas) por outras com URL
  // nova, de uma hora pra outra: o navegador via um <img src> novo e mostrava
  // o ícone quebrado até a imagem terminar de baixar (ou pior, se a troca de
  // DOM interrompia o download anterior no meio). Pré-carregando as imagens
  // do fragmento recebido *antes* de inserir no DOM, a troca só acontece
  // quando elas já estão prontas — sem flash de ícone quebrado.
  document.body.addEventListener("htmx:beforeSwap", function (evt) {
    var alvo = evt.detail && evt.detail.target;
    if (!alvo || alvo.id !== "chat-mensagens") return;
    var html = evt.detail.serverResponse;
    if (!html) return; // 304 ou corpo vazio: nada a trocar/pré-carregar

    var srcs = [];
    var re = /<img\b[^>]*\bsrc=(["'])(.*?)\1/gi;
    var m;
    while ((m = re.exec(html))) srcs.push(m[2]);
    if (!srcs.length) return; // sem imagens no fragmento: troca imediata (padrão do htmx)

    evt.detail.shouldSwap = false; // assume o swap manualmente, após o pré-carregamento
    var prontos = srcs.map(function (src) {
      return new Promise(function (resolve) {
        var pre = new Image();
        pre.onload = resolve;
        pre.onerror = resolve; // falha real de verdade não deve travar a troca
        pre.src = src;
      });
    });
    var limite = new Promise(function (resolve) { setTimeout(resolve, 2000); });
    Promise.race([Promise.all(prontos), limite]).then(function () {
      alvo.innerHTML = html;
    });
  });

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

# Patches de integração — migração para WUZAPI

> **Status (2026-09-02):** seções 1, 2, 3 e 5 — Fase 0 — já foram aplicadas
> em `app/` na branch `feat/whatsapp-provider-wuzapi` (commit
> `f57eb05`, `WHATSAPP_PROVIDER=meta` continua sendo o default, suíte
> passando). Este arquivo fica como registro do que foi feito e por quê.
> Só a **seção 4** (fila de notificações ativas) segue como proposta — entra
> quando as notificações forem ligadas (Fase 3 do plano de corte).

Tudo que precisa mudar no código existente. Os arquivos novos estão nesta
mesma pasta e são cópia-e-cola; o que está aqui são as **cinco edições** em
arquivos que já existem.

Ordem sugerida: 1 → 2 → 3 → 5 → (4 só quando as notificações ativas forem
ligadas).

---

## 1. `app/config.py` — variáveis novas

Inserir logo depois do bloco `# --- WhatsApp Cloud API (Meta) ---`
(hoje termina em `whatsapp_api_version`, linha ~117):

```python
    # --- Provedor de WhatsApp (migração WUZAPI, 2026-09-01) ---
    # "meta" = Cloud API (Graph); "wuzapi" = wuzapi/whatsmeow em container
    # próprio. Trocar aqui é o rollback inteiro: as credenciais dos dois
    # provedores convivem no .env e nenhum call site muda.
    whatsapp_provider: str = Field(default="meta")
    # URL INTERNA do wuzapi (http://wuzapi:8080). Nunca um endereço público:
    # a API é protegida só pelo header `Token`.
    wuzapi_base_url: str = Field(default="")
    # Token do usuário do wuzapi (header `Token` de todas as chamadas).
    wuzapi_token: str = Field(default="")
    # Chave HMAC (>= 32 chars) que assina os webhooks do wuzapi — mesmo papel
    # do WHATSAPP_APP_SECRET na Meta. Sem ela, o webhook é recusado em prod.
    wuzapi_webhook_hmac_key: str = Field(default="")
    wuzapi_timeout_s: float = Field(default=15.0)

    # --- Humanização da resposta (só tem efeito com provider=wuzapi) ---
    # Faixa do atraso de "digitando…" antes de cada mensagem do bot. Resposta
    # instantânea é o artefato de automação mais visível num número comum.
    whatsapp_digitacao_min_s: float = Field(default=1.0)
    whatsapp_digitacao_max_s: float = Field(default=2.5)

    # --- Fila de notificações ativas de chamado (outbox, migration 0091) ---
    whatsapp_notificacao_ativa: bool = Field(default=False)
    # Intervalo mínimo entre dois envios quaisquer + jitter aleatório.
    whatsapp_notificacao_intervalo_min_s: float = Field(default=2.0)
    whatsapp_notificacao_jitter_s: float = Field(default=3.0)
    # Intervalo mínimo entre dois envios para o MESMO número.
    whatsapp_notificacao_intervalo_contato_s: float = Field(default=120.0)
    # Janela de envio, hora local de São Paulo (fora dela a fila segura).
    whatsapp_notificacao_hora_inicio: int = Field(default=7)
    whatsapp_notificacao_hora_fim: int = Field(default=21)
```

---

## 2. `app/whatsapp_client.py` — substituir pelo adaptador

```bash
cp docs/wuzapi/whatsapp_client.py app/whatsapp_client.py
```

`enviar_mensagem_texto`, `enviar_documento` e `baixar_midia` mantêm nome e
assinatura, então `app/ia/whatsapp_intake.py` **não muda por causa disso**.
`tests/test_whatsapp_client.py` continua válido com um ajuste: as fixtures
precisam de `whatsapp_provider="meta"` (default já é `meta`, então na prática
só os testes novos do wuzapi entram).

Testes a acrescentar em `tests/test_whatsapp_client.py`:

- `wuzapi` manda `Token` no header e `{"Phone","Body"}` no corpo;
- `success: false` com HTTP 200 vira `WhatsAppEnvioFalhou`;
- `token_midia()` → `_descritor_do_token()` faz ida e volta;
- `baixar_midia` respeita `whatsapp_intake_midia_max_bytes` pelo `FileLength`
  declarado (sem baixar) e pelo binário decodificado;
- `digitando()` sai em `paused` mesmo quando o bloco levanta.

---

## 3. `app/ia/whatsapp_intake.py` — três edições

### 3.1 Entrada pública por mensagem já normalizada

O webhook do wuzapi entrega mensagem a mensagem (não um `entry/changes` como
a Meta). Trocar o corpo de `processar_mensagens_whatsapp` (linha ~298) e
expor o passo unitário:

```python
async def receber_mensagem_normalizada(msg: dict[str, Any]) -> None:
    """Grava e agenda UMA mensagem já no formato interno.

    É o ponto de entrada comum aos dois provedores: a Meta chega em lote
    (`processar_mensagens_whatsapp`), o wuzapi chega uma a uma
    (`app/routes/wuzapi.py`). Nunca lança — o webhook precisa do 200 rápido
    em qualquer um dos dois."""
    settings = get_settings()
    if not intake_ativo(settings):
        return
    try:
        await _receber_mensagem(msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("[WA INTAKE] Falha ao processar mensagem do webhook: %s", exc)


async def processar_mensagens_whatsapp(payload: dict[str, Any] | None) -> None:
    """(inalterada na descrição) Persiste as mensagens e agenda o processamento."""
    for msg in extrair_mensagens(payload):
        await receber_mensagem_normalizada(msg)
```

### 3.2 Resposta humanizada

Em `_responder` (linha ~635), trocar a função importada:

```python
async def _responder(telefone: str, texto: str) -> None:
    # `responder_humanizado` = presença "composing" + atraso de 1–2,5s + envio
    # + "paused". Com provider=meta, a presença é no-op e o atraso continua
    # valendo (não faz mal e mantém o comportamento igual nos dois).
    from app.whatsapp_client import responder_humanizado

    try:
        await responder_humanizado(telefone, texto)
    except Exception as exc:  # noqa: BLE001
        log.warning("[WA INTAKE] Falha ao responder no WhatsApp: %s", type(exc).__name__)
```

### 3.3 "Digitando…" durante a rodada do modelo

Em `_processar_conversa`, envolver a chamada do modelo (linha ~2424) —
é o trecho de 3 a 10 segundos em que hoje a conversa fica muda:

```python
    from app.whatsapp_client import digitando

    inicio = time.monotonic()
    async with digitando(telefone):
        saida, erro, tokens_in, tokens_out = await chamar_modelo_estruturado(
            mensagens,
            model=settings.whatsapp_intake_model,
            ...
        )
```

O mesmo vale para o retry da linha ~2568.

> Opcional, mas é o que fecha a ilusão: em `_receber_mensagem`, logo depois de
> `resolver_perfil_por_telefone` devolver um perfil, dispare
> `_agendar(get_client().presenca(telefone, "composing"))`. Aí o "digitando…"
> aparece no celular da pessoa em ~200ms, antes mesmo de a task do LLM
> começar — que é exatamente o que um atendente humano faz.

---

## 4. Notificações ativas (quando forem ligadas)

```bash
cp docs/wuzapi/notificacoes_whatsapp.py app/services/notificacoes_whatsapp.py
cp docs/wuzapi/0091_whatsapp_notificacoes_fila.sql supabase/migrations/
```

Nos pontos que hoje chamam `agendar_notificacao_email`
(`app/routes/workspace.py:989,1024`, `app/routes/portal.py:933`), acrescentar
— sem tirar o e-mail, que segue sendo o canal formal:

```python
    from app.services.notificacoes_whatsapp import (
        enfileirar_notificacao_chamado, texto_notificacao,
    )

    if solicitante_telefone:
        await enfileirar_notificacao_chamado(
            telefone=solicitante_telefone,
            corpo=texto_notificacao(
                codigo=chamado["codigo"],
                titulo=chamado["titulo"],
                autor_resposta=ctx.user.nome,
                link=f"{settings.app_base_url}/chamados/{chamado['id']}",
            ),
            perfil_id=chamado["cliente_id"],
            chamado_id=chamado["id"],
            dedup_key=f"chamado:{chamado['id']}:resposta",
        )
```

---

## 5. `app/main.py` — rota, worker e shutdown

```python
    from app.routes.wuzapi import register_wuzapi_routes
    register_wuzapi_routes(app)  # ao lado de register_whatsapp_routes(app)
```

No lifespan:

```python
    # startup
    from app.services.notificacoes_whatsapp import iniciar_fila_notificacoes
    iniciar_fila_notificacoes()

    yield

    # shutdown
    from app.services.notificacoes_whatsapp import parar_fila_notificacoes
    from app.whatsapp_client import fechar_cliente_http
    await parar_fila_notificacoes()
    await fechar_cliente_http()
```

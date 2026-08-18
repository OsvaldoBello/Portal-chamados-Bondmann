"""Claims sintéticas para criar chamados via WhatsApp sob RLS normal, sem
sessão HTTP de usuário.

Decisão de arquitetura do intake WhatsApp: `AtendimentoRepo.criar` roda sob
`app.db.rls_connection(claims)`, que só faz `SET LOCAL ROLE authenticated` +
`set_config('request.jwt.claims', ...)`. As policies de INSERT usadas aqui
(`chamados_insert`, `mensagens_insert`) só exigem `auth.uid()` (lido do `sub`
do JSON) batendo com `cliente_id`/`remetente_id` — não dependem de `role` no
JWT. Um dict `{"sub": perfil_id}` já é suficiente para a RLS continuar sendo
a barreira real de autorização (se o código do intake calcular `cliente_id`
errado por bug, o Postgres rejeita o INSERT).
"""

from __future__ import annotations


def claims_do_perfil(perfil_id: str) -> dict[str, str]:
    """Claims mínimas para `rls_connection`, escopadas a UM perfil já resolvido.

    NUNCA construir a partir de um campo vindo direto do payload do webhook
    (ex.: número de telefone) — só a partir de um `perfil_id` já resolvido e
    validado por `app.ia.whatsapp_intake.resolver_perfil_por_telefone`."""
    return {"sub": perfil_id}

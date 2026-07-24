-- 0056_fix_grants_ia_triagens_e_ia_worker.sql
--
-- Fecha dois buracos de GRANT que só se manifestam num Supabase LOCAL do zero
-- (`supabase start`, é assim que o CI "E2E RLS" / `tests/e2e -m rls` sobe o
-- banco) — em produção (iurlzlhbnoemkzgexcfk) ambos já "funcionavam" por
-- drift (privilégio aplicado fora de migration), o mesmo padrão de drift já
-- documentado nas migrations 0045/0046.
--
-- 1) `ia_triagens` (0050): a policy `ia_triagens_select_staff` existe, mas a
--    migration nunca fez `GRANT SELECT ... TO authenticated` na tabela — toda
--    outra migration deste repo que cria tabela nova faz esse GRANT explícito
--    (0004, 0008, 0015, 0017, 0034, 0055); a 0050 foi a exceção. RLS + policy
--    sem GRANT de tabela ⇒ "permission denied for table ia_triagens" antes
--    mesmo da policy ser avaliada. Em produção o SELECT já está concedido
--    (conferido via MCP); só o Supabase local partindo do zero não tem.
--
-- 2) `ia_worker` (0054): o role nasce sem ser membro de `postgres`. Em
--    produção isso nunca importa — `app/ia/contexto_quimico.py` abre uma
--    conexão PRÓPRIA para `IA_WORKER_DATABASE_URL` (login direto do role),
--    nunca faz `SET ROLE`. A suíte e2e (`tests/e2e/test_rls_base_quimico.py`)
--    simula o role a partir da conexão administrativa com
--    `SET LOCAL ROLE ia_worker` — e isso exige que o role da conexão
--    (`postgres`, que nas imagens recentes do Supabase CLI/hospedado não é
--    superuser) seja MEMBRO de `ia_worker`. O GRANT de membership não abre
--    nenhum privilégio novo para o `ia_worker` em si — ele continua sem
--    GRANT/policy em `base_quimico_formulacoes` (C7 intacto); só permite que
--    a conexão administrativa assuma o papel para fins de teste/depuração.
--
-- Idempotente.

GRANT SELECT ON ia_triagens TO authenticated;

GRANT ia_worker TO postgres;

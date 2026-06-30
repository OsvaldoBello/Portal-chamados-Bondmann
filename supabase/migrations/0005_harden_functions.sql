-- Fase 2 — Hardening de functions (advisors de segurança do Supabase)

-- search_path imutável no trigger_set_timestamp
ALTER FUNCTION trigger_set_timestamp() SET search_path = public;

-- Functions de trigger: executadas pelo mecanismo de trigger, nunca como RPC.
-- Remover EXECUTE de qualquer caller (não é necessário para o trigger disparar).
REVOKE ALL ON FUNCTION trigger_set_timestamp()  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION gerar_codigo_chamado()   FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION calcular_sla_chamado()   FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION handle_new_user()        FROM PUBLIC, anon, authenticated;

-- Helpers usados dentro das policies: precisam de EXECUTE pelo papel que
-- consulta as tabelas (authenticated). anon não tem policy que os use.
REVOKE ALL ON FUNCTION auth_role()       FROM PUBLIC, anon;
REVOKE ALL ON FUNCTION auth_empresa_id() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION auth_role()       TO authenticated;
GRANT EXECUTE ON FUNCTION auth_empresa_id() TO authenticated;

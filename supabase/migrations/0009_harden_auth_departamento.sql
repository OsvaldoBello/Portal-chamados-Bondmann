-- 0009_harden_auth_departamento.sql
-- Hardening do helper auth_departamento_id() (advisor 0028): remover EXECUTE do
-- PUBLIC/anon (não deve ser chamável sem login via /rpc), mantendo `authenticated`
-- — que É necessário para a avaliação das policies de RLS que usam a função.
-- Alinha o helper ao mesmo tratamento de auth_role()/auth_empresa_id().

REVOKE EXECUTE ON FUNCTION auth_departamento_id() FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION auth_departamento_id() TO authenticated;

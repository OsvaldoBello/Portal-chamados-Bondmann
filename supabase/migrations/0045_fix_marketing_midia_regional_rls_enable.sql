-- 0045_fix_marketing_midia_regional_rls_enable.sql — Sprint 0 / item 0.1
--
-- Achado durante a reconstrução da 0015 (validação "aplicar do zero" contra um
-- projeto de teste limpo): em produção (`iurlzlhbnoemkzgexcfk`), a tabela
-- `marketing_midia_regional` tem RLS HABILITADA (`relrowsecurity = true`),
-- mas NENHUMA migration deste repositório jamais executa
-- `ALTER TABLE marketing_midia_regional ENABLE ROW LEVEL SECURITY` — nem 0024
-- (criação da tabela), nem 0032, nem 0035 (que só adiciona as policies,
-- comentando que "a tabela já estava" com RLS habilitada). Ou seja: alguém
-- habilitou RLS nessa tabela fora de uma migration (SQL Editor do dashboard),
-- o mesmo tipo de drift que deixou a 0015 sem arquivo. Sem esta migration, uma
-- base aplicada do zero (0001→0044) fica com RLS DESABILITADA nessa tabela
-- (achado confirmado pelo advisor de segurança do Supabase: "RLS Disabled in
-- Public") — divergência real vs. produção, com policies órfãs (0035 já criou
-- `marketing_midia_regional_select`/`_admin`, mas RLS desligada as torna
-- inertes e a tabela fica 100% exposta a `anon`/`authenticated`).
--
-- Idempotente: ENABLE ROW LEVEL SECURITY não falha se já estiver habilitada.

ALTER TABLE marketing_midia_regional ENABLE ROW LEVEL SECURITY;

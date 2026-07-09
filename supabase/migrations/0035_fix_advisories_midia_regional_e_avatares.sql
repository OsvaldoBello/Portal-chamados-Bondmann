-- 0035_fix_advisories_midia_regional_e_avatares.sql
-- Dois achados do advisor de segurança do Supabase depois de aplicar 0029-0034:
--
-- 1) `marketing_midia_regional` tinha RLS HABILITADA mas NENHUMA policy
--    (bug pré-existente, não introduzido nesta sessão — a tabela já estava
--    assim antes das minhas migrations). Efeito prático: ninguém consegue
--    ler/escrever nela via `authenticated` (nem o dashboard, nem o CRUD novo
--    da Fase 6). Mesmo padrão de `feriados`: leitura por qualquer autenticado,
--    escrita só TI.
-- 2) O bucket público `avatares` (0033) ganhou uma policy de SELECT
--    (`avatares_select`) ampla demais — bucket público não precisa dela pra
--    servir os objetos (o endpoint `/storage/v1/object/public/...` não passa
--    por RLS); a policy só estava habilitando LISTAGEM de todos os arquivos
--    do bucket via API, sem necessidade nenhuma.

BEGIN;

CREATE POLICY marketing_midia_regional_select ON marketing_midia_regional
  FOR SELECT TO authenticated USING (true);

CREATE POLICY marketing_midia_regional_admin ON marketing_midia_regional
  FOR ALL TO authenticated USING (auth_is_ti()) WITH CHECK (auth_is_ti());

DROP POLICY IF EXISTS avatares_select ON storage.objects;

COMMIT;

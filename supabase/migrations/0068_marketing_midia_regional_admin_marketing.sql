-- 0068_marketing_midia_regional_admin_marketing.sql
-- Pedido do usuário (2026-07-31): o upload/CRUD da Mídia Regional
-- (`marketing_midia_regional`) só existia acessível ao TI (`/admin/gestao`,
-- gate `_require_ti`) — mas quem de fato recebe a planilha "Investimento por
-- Região" da agência todo mês e precisa subir o arquivo é o próprio time de
-- Marketing, não o TI. A rota web foi aberta para quem vê o Dashboard de
-- Marketing (`ctx.escopo == "Marketing"`, ver app/routes/admin.py), mas isso
-- sozinho não bastava: a policy `marketing_midia_regional_admin` (0035) só
-- libera `FOR ALL` pra `auth_is_ti()` — um ADMIN/OPERADOR do Marketing
-- continuaria barrado pela RLS na hora de gravar, mesmo com a rota liberada.
--
-- Mesmo padrão de `0052_avatares_admin_marketing.sql`: nova policy ADICIONAL
-- (não mexe na `marketing_midia_regional_admin` existente, que continua
-- valendo pro TI) liberando ADMIN do setor Marketing e o OPERADOR do
-- Marketing. SELECT já era aberto a todo `authenticated` (`marketing_midia_regional_select`,
-- inalterada).

BEGIN;

CREATE POLICY marketing_midia_regional_admin_marketing ON marketing_midia_regional
  FOR ALL TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM departamentos d
       WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
    )
    AND (SELECT auth_role()) IN ('ADMIN', 'OPERADOR')
  )
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM departamentos d
       WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
    )
    AND (SELECT auth_role()) IN ('ADMIN', 'OPERADOR')
  );

COMMIT;

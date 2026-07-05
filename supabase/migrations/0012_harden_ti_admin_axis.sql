-- 0012_harden_ti_admin_axis.sql
-- Auditoria de segurança (Etapa 3): unificar o eixo de "ACESSO TOTAL" em auth_is_ti().
--
-- As policies de gestão de `perfis` e `empresas` ainda usavam auth_role()='ADMIN'
-- (modelo pré-departamento da migration 0004), enquanto a 0010 já moveu a gestão de
-- catálogos (categorias/planos_sla/departamentos) para auth_is_ti(). Como o
-- provisionamento (registro_usuarios.sql) só concede role='ADMIN' a quem está em TI,
-- os dois coincidem NA PRÁTICA — mas a RLS não deve depender dessa disciplina:
--
--   Via latente fechada: um ADMIN mal-provisionado FORA de TI passaria em
--   perfis_admin_all (auth_role()='ADMIN') e poderia REESCREVER qualquer `perfis`,
--   inclusive o próprio departamento_id -> TI, ganhando acesso total (auth_is_ti()).
--
-- Após esta migration, só o staff de TI gere perfis e a config interna de empresas.
-- Sem regressão funcional: quem tinha acesso (ADMIN em TI) continua com auth_is_ti()=true.
-- As policies de SELECT (perfis_select, empresas_select) permanecem inalteradas.

-- perfis — gestão (INSERT/UPDATE/DELETE) restrita ao acesso total (TI)
DROP POLICY perfis_admin_all ON perfis;
CREATE POLICY perfis_admin_all ON perfis
  FOR ALL TO authenticated
  USING (auth_is_ti())
  WITH CHECK (auth_is_ti());

-- empresas — config interna de SLA, gerida só pelo acesso total (TI)
DROP POLICY empresas_admin_all ON empresas;
CREATE POLICY empresas_admin_all ON empresas
  FOR ALL TO authenticated
  USING (auth_is_ti())
  WITH CHECK (auth_is_ti());

-- 0014_rls_initplan_optimize.sql
-- Otimização de RLS (advisor 0003_auth_rls_initplan): envolver as chamadas a
-- auth.*()/helpers em (SELECT ...) para o Postgres avaliá-las UMA vez por query
-- (InitPlan) em vez de por linha. Semântica IDÊNTICA (funções STABLE, sem args;
-- os claims são constantes na transação). Revalidado e2e (RH só vê RH; TI tudo;
-- autor não recebe nota interna) após aplicar.

-- chamados
DROP POLICY chamados_insert ON chamados;
CREATE POLICY chamados_insert ON chamados FOR INSERT TO authenticated
  WITH CHECK ((cliente_id = (SELECT auth.uid())) AND (departamento_id IS NOT NULL));

DROP POLICY chamados_select ON chamados;
CREATE POLICY chamados_select ON chamados FOR SELECT TO authenticated
  USING ((cliente_id = (SELECT auth.uid())) OR (SELECT auth_is_ti())
         OR (((SELECT auth_departamento_id()) IS NOT NULL)
             AND (departamento_id = (SELECT auth_departamento_id()))));

DROP POLICY chamados_update_cliente_avaliacao ON chamados;
CREATE POLICY chamados_update_cliente_avaliacao ON chamados FOR UPDATE TO authenticated
  USING (((SELECT auth_role()) = 'CLIENTE'::papel_usuario) AND (cliente_id = (SELECT auth.uid()))
         AND (status = 'RESOLVIDO'::status_chamado))
  WITH CHECK (((SELECT auth_role()) = 'CLIENTE'::papel_usuario) AND (cliente_id = (SELECT auth.uid()))
         AND (status = 'RESOLVIDO'::status_chamado));

DROP POLICY chamados_update_staff ON chamados;
CREATE POLICY chamados_update_staff ON chamados FOR UPDATE TO authenticated
  USING ((SELECT auth_is_ti()) OR (((SELECT auth_departamento_id()) IS NOT NULL)
         AND (departamento_id = (SELECT auth_departamento_id()))))
  WITH CHECK ((SELECT auth_is_ti()) OR (((SELECT auth_departamento_id()) IS NOT NULL)
         AND (departamento_id = (SELECT auth_departamento_id()))));

-- mensagens
DROP POLICY mensagens_select ON mensagens;
CREATE POLICY mensagens_select ON mensagens FOR SELECT TO authenticated
  USING (EXISTS (SELECT 1 FROM chamados c WHERE c.id = mensagens.chamado_id
    AND ((SELECT auth_is_ti())
         OR (((SELECT auth_departamento_id()) IS NOT NULL) AND (c.departamento_id = (SELECT auth_departamento_id())))
         OR ((c.cliente_id = (SELECT auth.uid())) AND (mensagens.is_interna = false)))));

DROP POLICY mensagens_insert ON mensagens;
CREATE POLICY mensagens_insert ON mensagens FOR INSERT TO authenticated
  WITH CHECK ((remetente_id = (SELECT auth.uid())) AND EXISTS (SELECT 1 FROM chamados c WHERE c.id = mensagens.chamado_id
    AND ((SELECT auth_is_ti())
         OR (((SELECT auth_departamento_id()) IS NOT NULL) AND (c.departamento_id = (SELECT auth_departamento_id())))
         OR ((c.cliente_id = (SELECT auth.uid())) AND (mensagens.is_interna = false)))));

-- historico_chamados
DROP POLICY historico_select ON historico_chamados;
CREATE POLICY historico_select ON historico_chamados FOR SELECT TO authenticated
  USING (EXISTS (SELECT 1 FROM chamados c WHERE c.id = historico_chamados.chamado_id
    AND ((c.cliente_id = (SELECT auth.uid())) OR (SELECT auth_is_ti())
         OR (((SELECT auth_departamento_id()) IS NOT NULL) AND (c.departamento_id = (SELECT auth_departamento_id()))))));

DROP POLICY historico_insert ON historico_chamados;
CREATE POLICY historico_insert ON historico_chamados FOR INSERT TO authenticated
  WITH CHECK (((ator_id = (SELECT auth.uid())) OR (ator_id IS NULL)) AND EXISTS (SELECT 1 FROM chamados c
    WHERE c.id = historico_chamados.chamado_id
    AND ((c.cliente_id = (SELECT auth.uid())) OR (SELECT auth_is_ti())
         OR (((SELECT auth_departamento_id()) IS NOT NULL) AND (c.departamento_id = (SELECT auth_departamento_id()))))));

-- perfis
DROP POLICY perfis_select ON perfis;
CREATE POLICY perfis_select ON perfis FOR SELECT TO authenticated
  USING ((id = (SELECT auth.uid())) OR ((SELECT auth_role()) = 'OPERADOR'::papel_usuario)
         OR (empresa_id = (SELECT auth_empresa_id())));

DROP POLICY perfis_admin_all ON perfis;
CREATE POLICY perfis_admin_all ON perfis FOR ALL TO authenticated
  USING ((SELECT auth_is_ti())) WITH CHECK ((SELECT auth_is_ti()));

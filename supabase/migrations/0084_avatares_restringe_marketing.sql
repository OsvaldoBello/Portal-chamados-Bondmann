-- 0084_avatares_restringe_marketing.sql
-- Pedido do usuário (2026-08-17): a foto de perfil de OUTRO usuário só devia
-- poder ser trocada por TI ou Marketing — mas a policy da 0052 abriu para
-- QUALQUER ADMIN de setor, inclusive setores sem fila (ex.: Compras), onde o
-- recurso nem faz sentido (e nem devia aparecer na tela — ver também o ajuste
-- em `app/routes/admin.py::admin_context`/`AdminCtx.pode_editar_avatares`).
--
-- TI segue coberto por `perfis_admin_all` (0012), que já libera qualquer
-- coluna independente desta policy. Aqui fechamos
-- `perfis_update_avatar_staff` ao Marketing (ADMIN ou OPERADOR do próprio
-- setor), no lugar de "qualquer ADMIN".

BEGIN;

DROP POLICY IF EXISTS perfis_update_avatar_staff ON perfis;

CREATE POLICY perfis_update_avatar_staff ON perfis
  FOR UPDATE TO authenticated
  USING (
    (SELECT auth_role()) IN ('ADMIN', 'OPERADOR')
    AND EXISTS (
      SELECT 1 FROM departamentos d
       WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
    )
  )
  WITH CHECK (
    (SELECT auth_role()) IN ('ADMIN', 'OPERADOR')
    AND EXISTS (
      SELECT 1 FROM departamentos d
       WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
    )
  );

COMMIT;

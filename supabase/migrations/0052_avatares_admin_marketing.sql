-- 0052_avatares_admin_marketing.sql
-- Pedido do usuário (2026-07-23): além do TI, qualquer ADMIN (de qualquer
-- setor) e o OPERADOR do Marketing passam a poder enviar/trocar a foto de
-- perfil de usuários JÁ CRIADOS (não só a própria, e não só na criação da
-- conta).
--
-- Até aqui só existiam duas policies de UPDATE em `perfis`:
--   - perfis_admin_all (0012, FOR ALL)  — só TI (auth_is_ti()), qualquer coluna.
--   - perfis_update_self (0033)         — qualquer autenticado, mas só a
--     PRÓPRIA linha (USING id = auth.uid()).
-- Isso significa que hoje um ADMIN de setor (não-TI) tentando gravar o avatar
-- de OUTRO usuário é filtrado pela própria RLS antes mesmo de chegar no
-- trigger `enforce_perfil_self_so_avatar` (0033) — a policy de self nunca
-- alcança a linha alheia.
--
-- Nova policy dá a ADMIN/OPERADOR-do-Marketing a mesma abertura de ALVO (
-- qualquer linha), mas quem continua restringindo a COLUNA é o trigger 0033:
-- como `auth_is_ti()` é falso pra esses papéis, o trigger segue barrando
-- qualquer coluna fora `avatar_path`/`updated_at` — o avatar é a ÚNICA coisa
-- que esses papéis conseguem alterar no perfil de outra pessoa. Sem mudança
-- no trigger em si.

BEGIN;

CREATE POLICY perfis_update_avatar_staff ON perfis
  FOR UPDATE TO authenticated
  USING (
    (SELECT auth_role()) = 'ADMIN'
    OR (
      (SELECT auth_role()) = 'OPERADOR'
      AND EXISTS (
        SELECT 1 FROM departamentos d
         WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
      )
    )
  )
  WITH CHECK (
    (SELECT auth_role()) = 'ADMIN'
    OR (
      (SELECT auth_role()) = 'OPERADOR'
      AND EXISTS (
        SELECT 1 FROM departamentos d
         WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
      )
    )
  );

COMMIT;

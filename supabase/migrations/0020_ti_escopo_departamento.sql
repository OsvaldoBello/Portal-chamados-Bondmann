-- 0020_ti_escopo_departamento.sql
-- Decisão de produto 2026-07-06: o staff de TI passa a VER apenas os chamados do
-- próprio departamento (TI), como qualquer outro setor — NÃO mais "acesso total"
-- de visibilidade. O TI MANTÉM os poderes de administrador (gestão de catálogos,
-- usuários, planos e o REPASSE de chamados entre setores).
--
-- Como fica:
--   * Visibilidade de chamados/mensagens/histórico: autor OU staff do MESMO setor.
--     (TI tem departamento_id = TI, então enxerga os chamados do setor TI.)
--   * auth_is_ti() continua valendo para GESTÃO (categorias/departamentos/planos/
--     perfis/empresas — migrations 0010/0012, inalteradas aqui).
--   * REPASSE entre setores: mantido só para o TI via WITH CHECK de auth_is_ti()
--     na policy de UPDATE (permite gravar um departamento_id fora do setor atual).
--
-- Reverte apenas o ramo de VISIBILIDADE `auth_is_ti()` introduzido na 0010.

BEGIN;

-- ============================================================
-- chamados — visibilidade: autor OU staff do mesmo setor (sem all-access do TI)
-- ============================================================
DROP POLICY chamados_select       ON chamados;
DROP POLICY chamados_update_staff ON chamados;

CREATE POLICY chamados_select ON chamados
  FOR SELECT TO authenticated
  USING (
    cliente_id = auth.uid()                                            -- autor vê os próprios
    OR (auth_departamento_id() IS NOT NULL
        AND departamento_id = auth_departamento_id())                 -- staff vê o SEU setor (inclui TI)
  );

-- UPDATE: staff só age nos chamados do seu setor (USING). O WITH CHECK mantém
-- auth_is_ti() para permitir ao TI gravar um departamento_id de OUTRO setor —
-- é o repasse (transferir). Demais campos do próprio setor seguem liberados.
CREATE POLICY chamados_update_staff ON chamados
  FOR UPDATE TO authenticated
  USING (
    auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id()
  )
  WITH CHECK (
    auth_is_ti()
    OR (auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id())
  );

-- ============================================================
-- mensagens — mesmo escopo; funcionário-autor só vê/insere pública
-- ============================================================
DROP POLICY mensagens_select ON mensagens;
DROP POLICY mensagens_insert ON mensagens;

CREATE POLICY mensagens_select ON mensagens
  FOR SELECT TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = mensagens.chamado_id
        AND (
          (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
        )
    )
  );

CREATE POLICY mensagens_insert ON mensagens
  FOR INSERT TO authenticated
  WITH CHECK (
    remetente_id = auth.uid()
    AND EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = mensagens.chamado_id
        AND (
          (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
        )
    )
  );

-- ============================================================
-- historico — mesmo escopo (autor OU staff do setor)
-- ============================================================
DROP POLICY historico_select ON historico_chamados;
DROP POLICY historico_insert ON historico_chamados;

CREATE POLICY historico_select ON historico_chamados
  FOR SELECT TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = historico_chamados.chamado_id
        AND (
          c.cliente_id = auth.uid()
          OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
        )
    )
  );

CREATE POLICY historico_insert ON historico_chamados
  FOR INSERT TO authenticated
  WITH CHECK (
    (ator_id = auth.uid() OR ator_id IS NULL)
    AND EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = historico_chamados.chamado_id
        AND (
          c.cliente_id = auth.uid()
          OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
        )
    )
  );

COMMIT;

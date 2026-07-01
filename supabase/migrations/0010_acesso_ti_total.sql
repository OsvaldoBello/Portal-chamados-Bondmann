-- 0010_acesso_ti_total.sql
-- Refinamento do modelo de acesso (logística de usuários — decisão 2026-07-01):
--   * TI            -> ACESSO TOTAL ao sistema (vê/atende todos os chamados; gere catálogos).
--   * RH / Marketing -> veem os chamados do SEU departamento + os que ELES abriram;
--                       podem abrir chamados para QUALQUER departamento.
--   * Funcionário    -> cria chamados e vê APENAS os que criou.
--
-- Substitui o conceito anterior "super-admin = ADMIN com departamento NULO"
-- (migration 0008) por "acesso total = staff no departamento TI".
-- "Staff" = perfil com role OPERADOR/ADMIN e um departamento; funcionário = CLIENTE.

-- ============================================================
-- 1. Helper: o usuário atual é do departamento TI? (acesso total)
-- ============================================================
CREATE OR REPLACE FUNCTION auth_is_ti()
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.perfis p
    JOIN public.departamentos d ON d.id = p.departamento_id
    WHERE p.id = auth.uid() AND d.nome = 'TI'
  );
$$;
REVOKE EXECUTE ON FUNCTION auth_is_ti() FROM PUBLIC, anon;
GRANT  EXECUTE ON FUNCTION auth_is_ti() TO authenticated;

-- ============================================================
-- 2. chamados — visibilidade: autor OU TI OU staff do departamento
-- ============================================================
DROP POLICY chamados_select        ON chamados;
DROP POLICY chamados_update_staff  ON chamados;
-- chamados_insert permanece (cliente_id = auth.uid() AND departamento_id IS NOT NULL):
-- qualquer autenticado (funcionário ou staff) abre para qualquer departamento.

CREATE POLICY chamados_select ON chamados
  FOR SELECT TO authenticated
  USING (
    cliente_id = auth.uid()                                             -- autor vê os próprios
    OR auth_is_ti()                                                     -- TI vê tudo
    OR (auth_departamento_id() IS NOT NULL
        AND departamento_id = auth_departamento_id())                  -- staff vê o seu setor
  );

CREATE POLICY chamados_update_staff ON chamados
  FOR UPDATE TO authenticated
  USING (
    auth_is_ti()
    OR (auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id())
  )
  WITH CHECK (
    auth_is_ti()
    OR (auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id())
  );

-- ============================================================
-- 3. mensagens — mesmo escopo; funcionário-autor só vê/insere pública
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
          auth_is_ti()
          OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
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
          auth_is_ti()
          OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
        )
    )
  );

-- ============================================================
-- 4. historico — mesmo escopo
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
          OR auth_is_ti()
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
          OR auth_is_ti()
          OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
        )
    )
  );

-- ============================================================
-- 5. Gestão de catálogos = acesso total (TI). Substitui a checagem de role ADMIN.
-- ============================================================
DROP POLICY departamentos_admin_all ON departamentos;
CREATE POLICY departamentos_admin_all ON departamentos
  FOR ALL TO authenticated USING (auth_is_ti()) WITH CHECK (auth_is_ti());

DROP POLICY categorias_admin_all ON categorias;
CREATE POLICY categorias_admin_all ON categorias
  FOR ALL TO authenticated USING (auth_is_ti()) WITH CHECK (auth_is_ti());

DROP POLICY planos_sla_admin_all ON planos_sla;
CREATE POLICY planos_sla_admin_all ON planos_sla
  FOR ALL TO authenticated USING (auth_is_ti()) WITH CHECK (auth_is_ti());

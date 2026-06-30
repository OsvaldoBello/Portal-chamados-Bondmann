-- Fase 2 / Seção 3.3 — RLS por papel nas 7 tabelas + helpers + grants
-- Papéis: ADMIN (irrestrito), OPERADOR (todos os chamados/mensagens),
-- CLIENTE (apenas própria empresa; sem is_interna; sem update de status).

-- ============================================================
-- Helpers (SECURITY DEFINER p/ evitar recursão de RLS ao ler perfis)
-- ============================================================
CREATE OR REPLACE FUNCTION auth_role()
RETURNS papel_usuario
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT role FROM public.perfis WHERE id = auth.uid();
$$;

CREATE OR REPLACE FUNCTION auth_empresa_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT empresa_id FROM public.perfis WHERE id = auth.uid();
$$;

-- ============================================================
-- Habilitar RLS
-- ============================================================
ALTER TABLE planos_sla         ENABLE ROW LEVEL SECURITY;
ALTER TABLE empresas           ENABLE ROW LEVEL SECURITY;
ALTER TABLE perfis             ENABLE ROW LEVEL SECURITY;
ALTER TABLE categorias         ENABLE ROW LEVEL SECURITY;
ALTER TABLE chamados           ENABLE ROW LEVEL SECURITY;
ALTER TABLE mensagens          ENABLE ROW LEVEL SECURITY;
ALTER TABLE historico_chamados ENABLE ROW LEVEL SECURITY;
ALTER TABLE contador_chamados  ENABLE ROW LEVEL SECURITY;  -- sem policy: só a function SECURITY DEFINER acessa

-- ============================================================
-- planos_sla — gestão exclusiva do ADMIN
-- ============================================================
CREATE POLICY planos_sla_admin_all ON planos_sla
  FOR ALL TO authenticated
  USING (auth_role() = 'ADMIN')
  WITH CHECK (auth_role() = 'ADMIN');

-- ============================================================
-- empresas — ADMIN tudo; OPERADOR lê todas; CLIENTE lê só a sua
-- ============================================================
CREATE POLICY empresas_admin_all ON empresas
  FOR ALL TO authenticated
  USING (auth_role() = 'ADMIN')
  WITH CHECK (auth_role() = 'ADMIN');

CREATE POLICY empresas_select ON empresas
  FOR SELECT TO authenticated
  USING (auth_role() = 'OPERADOR' OR id = auth_empresa_id());

-- ============================================================
-- perfis — ADMIN tudo; OPERADOR lê todos; CLIENTE lê self + mesma empresa
--   (UPDATE só ADMIN, para impedir auto-escalada de role pelo cliente)
-- ============================================================
CREATE POLICY perfis_admin_all ON perfis
  FOR ALL TO authenticated
  USING (auth_role() = 'ADMIN')
  WITH CHECK (auth_role() = 'ADMIN');

CREATE POLICY perfis_select ON perfis
  FOR SELECT TO authenticated
  USING (
    id = auth.uid()
    OR auth_role() = 'OPERADOR'
    OR empresa_id = auth_empresa_id()
  );

-- ============================================================
-- categorias — catálogo global: todos autenticados leem; ADMIN gere
-- ============================================================
CREATE POLICY categorias_admin_all ON categorias
  FOR ALL TO authenticated
  USING (auth_role() = 'ADMIN')
  WITH CHECK (auth_role() = 'ADMIN');

CREATE POLICY categorias_select ON categorias
  FOR SELECT TO authenticated
  USING (true);

-- ============================================================
-- chamados — ADMIN tudo; OPERADOR vê/edita todos; CLIENTE só própria empresa
-- ============================================================
CREATE POLICY chamados_admin_all ON chamados
  FOR ALL TO authenticated
  USING (auth_role() = 'ADMIN')
  WITH CHECK (auth_role() = 'ADMIN');

CREATE POLICY chamados_select ON chamados
  FOR SELECT TO authenticated
  USING (auth_role() = 'OPERADOR' OR empresa_id = auth_empresa_id());

CREATE POLICY chamados_insert ON chamados
  FOR INSERT TO authenticated
  WITH CHECK (
    auth_role() = 'OPERADOR'
    OR (cliente_id = auth.uid() AND empresa_id = auth_empresa_id())
  );

-- OPERADOR atualiza (status/prioridade/operador). CLIENTE não atualiza.
CREATE POLICY chamados_update_operador ON chamados
  FOR UPDATE TO authenticated
  USING (auth_role() = 'OPERADOR')
  WITH CHECK (auth_role() = 'OPERADOR');

-- ============================================================
-- mensagens — CLIENTE: só chamados da sua empresa E is_interna=false
--             OPERADOR/ADMIN: todas. Imutáveis (sem UPDATE/DELETE).
-- ============================================================
CREATE POLICY mensagens_select ON mensagens
  FOR SELECT TO authenticated
  USING (
    auth_role() IN ('ADMIN','OPERADOR')
    OR (
      is_interna = false
      AND EXISTS (
        SELECT 1 FROM chamados c
        WHERE c.id = mensagens.chamado_id
          AND c.empresa_id = auth_empresa_id()
      )
    )
  );

CREATE POLICY mensagens_insert ON mensagens
  FOR INSERT TO authenticated
  WITH CHECK (
    remetente_id = auth.uid()
    AND (
      auth_role() IN ('ADMIN','OPERADOR')
      OR (
        is_interna = false
        AND EXISTS (
          SELECT 1 FROM chamados c
          WHERE c.id = mensagens.chamado_id
            AND c.empresa_id = auth_empresa_id()
        )
      )
    )
  );

-- ============================================================
-- historico_chamados — auditoria imutável (apenas SELECT/INSERT)
--   CLIENTE: histórico de chamados da própria empresa
--   OPERADOR/ADMIN: todos
-- ============================================================
CREATE POLICY historico_select ON historico_chamados
  FOR SELECT TO authenticated
  USING (
    auth_role() IN ('ADMIN','OPERADOR')
    OR EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = historico_chamados.chamado_id
        AND c.empresa_id = auth_empresa_id()
    )
  );

CREATE POLICY historico_insert ON historico_chamados
  FOR INSERT TO authenticated
  WITH CHECK (
    (ator_id = auth.uid() OR ator_id IS NULL)
    AND (
      auth_role() IN ('ADMIN','OPERADOR')
      OR EXISTS (
        SELECT 1 FROM chamados c
        WHERE c.id = historico_chamados.chamado_id
          AND c.empresa_id = auth_empresa_id()
      )
    )
  );

-- ============================================================
-- Grants — anon nunca toca domínio; authenticated mediado por RLS.
--   historico e mensagens recebem apenas SELECT/INSERT (imutabilidade).
-- ============================================================
REVOKE ALL ON planos_sla, empresas, perfis, categorias,
              chamados, mensagens, historico_chamados, contador_chamados
  FROM anon;
REVOKE ALL ON contador_chamados FROM authenticated;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  planos_sla, empresas, perfis, categorias, chamados
  TO authenticated;
GRANT SELECT, INSERT ON mensagens, historico_chamados TO authenticated;

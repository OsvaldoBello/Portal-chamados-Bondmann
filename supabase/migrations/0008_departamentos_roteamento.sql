-- 0008_departamentos_roteamento.sql
-- Pivô para SISTEMA INTERNO (Fase 3):
--   * Chamados deixam de ser isolados por empresa (modelo SaaS) e passam a ser
--     ROTEADOS POR DEPARTAMENTO (TI / RH / Marketing). O staff de cada
--     departamento só vê os chamados endereçados ao seu setor; um super-admin
--     (ADMIN com departamento NULO) enxerga todos.
--   * O AUTOR (funcionário) vê os próprios chamados, para qualquer departamento.
--   * `empresas`/`planos_sla` permanecem apenas como CONFIG INTERNA de SLA
--     (org única = Bondmann); sem venda/gestão self-service.
--   * Cadastro passa a ser DIRETO no Supabase (sem signup público) — handle_new_user
--     vincula o novo usuário à org interna com papel CLIENTE; admin promove depois.
-- Decisões: MD Seções 3.2 / 3.3 / 5.1 (atualizadas antes desta migration).

-- ============================================================
-- 1. Departamentos (gerenciável, como categorias)
-- ============================================================
CREATE TABLE departamentos (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome       text NOT NULL UNIQUE,
  ativo      boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO departamentos (nome) VALUES ('TI'), ('RH'), ('Marketing')
ON CONFLICT (nome) DO NOTHING;

-- ============================================================
-- 2. Vínculos de departamento
--    perfis.departamento_id  -> setor do STAFF (OPERADOR/ADMIN). NULL = super-admin
--                               (ADMIN) ou funcionário comum (CLIENTE).
--    chamados.departamento_id -> setor de DESTINO do chamado.
-- ============================================================
ALTER TABLE perfis   ADD COLUMN departamento_id uuid REFERENCES departamentos(id) ON DELETE SET NULL;
ALTER TABLE chamados ADD COLUMN departamento_id uuid REFERENCES departamentos(id) ON DELETE RESTRICT;
CREATE INDEX idx_perfis_departamento    ON perfis(departamento_id);
CREATE INDEX idx_chamados_departamento  ON chamados(departamento_id);
CREATE INDEX idx_chamados_depto_status  ON chamados(departamento_id, status);

-- Backfill: chamados pré-existentes sem destino vão para TI (não quebra RLS nova).
UPDATE chamados
   SET departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI')
 WHERE departamento_id IS NULL;

-- ============================================================
-- 3. Seed da org interna única + plano de SLA default (config de SLA)
-- ============================================================
INSERT INTO planos_sla (
  nome, resposta_baixa_min, resposta_media_min, resposta_alta_min,
  resolucao_baixa_min, resolucao_media_min, resolucao_alta_min,
  resposta_default_min, resolucao_default_min
)
SELECT 'Padrão Interno', 480, 240, 120, 4320, 2880, 1440, 720, 1440
WHERE NOT EXISTS (SELECT 1 FROM planos_sla);

INSERT INTO empresas (nome_fantasia, plano_sla_id)
SELECT 'Bondmann Química', (SELECT id FROM planos_sla ORDER BY created_at LIMIT 1)
WHERE NOT EXISTS (SELECT 1 FROM empresas);

-- ============================================================
-- 4. Helper: departamento do usuário atual (SECURITY DEFINER, evita recursão RLS)
-- ============================================================
CREATE OR REPLACE FUNCTION auth_departamento_id()
RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public
AS $$ SELECT departamento_id FROM public.perfis WHERE id = auth.uid(); $$;

-- ============================================================
-- 5. Departamentos: RLS — todos autenticados leem (dropdown); super-admin gere
-- ============================================================
ALTER TABLE departamentos ENABLE ROW LEVEL SECURITY;

CREATE POLICY departamentos_select ON departamentos
  FOR SELECT TO authenticated USING (true);

CREATE POLICY departamentos_admin_all ON departamentos
  FOR ALL TO authenticated
  USING (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
  WITH CHECK (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL);

REVOKE ALL ON departamentos FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON departamentos TO authenticated;

-- ============================================================
-- 6. chamados — repontar isolamento de empresa -> departamento/autor
-- ============================================================
DROP POLICY chamados_admin_all       ON chamados;
DROP POLICY chamados_select          ON chamados;
DROP POLICY chamados_insert          ON chamados;
DROP POLICY chamados_update_operador ON chamados;
-- (chamados_update_cliente_avaliacao permanece — migration 0006)

-- Visibilidade: autor vê os próprios; staff vê o seu departamento; super-admin vê tudo.
CREATE POLICY chamados_select ON chamados
  FOR SELECT TO authenticated
  USING (
    cliente_id = auth.uid()
    OR (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
    OR (auth_role() IN ('OPERADOR','ADMIN') AND departamento_id = auth_departamento_id())
  );

-- Abertura: qualquer autenticado abre como si mesmo, escolhendo o departamento de destino.
CREATE POLICY chamados_insert ON chamados
  FOR INSERT TO authenticated
  WITH CHECK (cliente_id = auth.uid() AND departamento_id IS NOT NULL);

-- Atendimento: staff no escopo (ou super-admin) atualiza status/prioridade/operador.
CREATE POLICY chamados_update_staff ON chamados
  FOR UPDATE TO authenticated
  USING (
    (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
    OR (auth_role() IN ('OPERADOR','ADMIN') AND departamento_id = auth_departamento_id())
  )
  WITH CHECK (
    (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
    OR (auth_role() IN ('OPERADOR','ADMIN') AND departamento_id = auth_departamento_id())
  );

-- ============================================================
-- 7. mensagens / historico — mesmo escopo, derivado do chamado
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
          (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
          OR (auth_role() IN ('OPERADOR','ADMIN') AND c.departamento_id = auth_departamento_id())
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
          (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
          OR (auth_role() IN ('OPERADOR','ADMIN') AND c.departamento_id = auth_departamento_id())
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
        )
    )
  );

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
          OR (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
          OR (auth_role() IN ('OPERADOR','ADMIN') AND c.departamento_id = auth_departamento_id())
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
          OR (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
          OR (auth_role() IN ('OPERADOR','ADMIN') AND c.departamento_id = auth_departamento_id())
        )
    )
  );

-- ============================================================
-- 8. Trava de coluna do CLIENTE — incluir departamento_id no conjunto imutável
--    (o autor só altera a avaliação; não pode redirecionar o chamado).
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF auth_role() = 'CLIENTE' THEN
    IF ROW(NEW.codigo, NEW.empresa_id, NEW.departamento_id, NEW.cliente_id, NEW.operador_id,
           NEW.categoria_id, NEW.titulo, NEW.descricao, NEW.status, NEW.prioridade,
           NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
           NEW.resolvido_em, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.codigo, OLD.empresa_id, OLD.departamento_id, OLD.cliente_id, OLD.operador_id,
           OLD.categoria_id, OLD.titulo, OLD.descricao, OLD.status, OLD.prioridade,
           OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
           OLD.resolvido_em, OLD.created_at)
    THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION enforce_cliente_so_avaliacao() FROM PUBLIC, anon, authenticated;

-- ============================================================
-- 9. Cadastro direto no Supabase — vincula à org interna única, papel CLIENTE.
--    (Admin promove role/departamento depois; ver supabase/registro_usuarios.sql.)
-- ============================================================
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public
AS $$
BEGIN
  INSERT INTO perfis (id, nome, role, empresa_id)
  VALUES (
    NEW.id,
    COALESCE(NEW.raw_user_meta_data ->> 'nome', NEW.email),
    'CLIENTE',
    (SELECT id FROM empresas ORDER BY created_at LIMIT 1)
  )
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

-- 0075_marketing_setor_baseline.sql
-- Pedido do usuário (2026-08-03): os meses pré-Portal TÊM setor solicitante
-- atribuído — jan-mai no dashboard estático e junho na planilha "Indicadores
-- (2).xlsx". Até agora a aba "4. Solicitantes" só tinha a quebra por setor a
-- partir de chamado real do Portal (`vw_marketing_setor_mensal`), então
-- jan-jun/26 apareciam praticamente vazios no ranking.
--
-- Esta tabela é para `vw_marketing_setor_mensal` o que a
-- `marketing_volume_baseline` (0069/0073) é para `vw_marketing_volume_mensal`:
-- guarda o agregado histórico e SUBSTITUI o cálculo da view nos meses em que
-- tem linha. Mesmo princípio — dado agregado, não chamados fabricados.
--
-- FONTE: a planilha (abas `DemandasJAN`..`DemandasJUN`, coluna **Departamento**),
-- não o HTML. Os dois divergem em FEV e ABR — as mesmas divergências que a
-- `0074` já corrigiu no volume —, e o HTML descartava as linhas sem setor
-- preenchido, o que fazia FEV somar 35 (contra 36 demandas) e ABR somar 47
-- (contra 48). Aqui os seis meses fecham exatamente com o total de demandas do
-- mês; as duas linhas sem setor (uma em FEV, uma em ABR gravada como "-")
-- entram como **"Não informado"** em vez de sumirem.
--
-- ⚠️ "Departamento" NÃO é "Origem da Demanda" — são colunas diferentes da
-- planilha e divergem em 4 dos 6 meses (JAN: 8 × 15). "Departamento" é o setor
-- PARA QUEM a peça foi feita (o card de aniversariantes é do RH); "Origem" é de
-- quem partiu a INICIATIVA (o Marketing propôs, ninguém pediu). Em jan/26 são 7
-- peças feitas para Comercial/RH/Compras por iniciativa do próprio Marketing.
-- O ranking de solicitantes usa Departamento (é o que o título diz); a aba
-- "3. Origem da Demanda" segue usando `mkt_orig`/`sol_orig`. A aba mostra os
-- dois números lado a lado para a diferença ficar explícita (decisão do
-- usuário, 2026-08-03) — ver `admin_marketing.js::renderDept`.
--
-- Nomes alinhados com `departamentos` do Portal para as barras se fundirem
-- entre meses históricos e meses de Portal: "Gerentes Comerciais" →
-- "Gerentes de vendas" e "Supervisão Comercial" → "Supervisão de Vendas".
--
-- RLS: mesmo padrão de `marketing_volume_baseline` (0069).

BEGIN;

CREATE TABLE IF NOT EXISTS marketing_setor_baseline (
  mes        date    NOT NULL,
  setor      text    NOT NULL,
  total      integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (mes, setor)
);

ALTER TABLE marketing_setor_baseline ENABLE ROW LEVEL SECURITY;

CREATE POLICY marketing_setor_baseline_select ON marketing_setor_baseline
  FOR SELECT TO authenticated USING (true);

CREATE POLICY marketing_setor_baseline_admin ON marketing_setor_baseline
  FOR ALL TO authenticated
  USING (
    auth_is_ti()
    OR (
      EXISTS (
        SELECT 1 FROM departamentos d
         WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
      )
      AND (SELECT auth_role()) IN ('ADMIN', 'OPERADOR')
    )
  )
  WITH CHECK (
    auth_is_ti()
    OR (
      EXISTS (
        SELECT 1 FROM departamentos d
         WHERE d.id = (SELECT auth_departamento_id()) AND d.nome = 'Marketing'
      )
      AND (SELECT auth_role()) IN ('ADMIN', 'OPERADOR')
    )
  );

REVOKE ALL ON marketing_setor_baseline FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON marketing_setor_baseline TO authenticated;

INSERT INTO marketing_setor_baseline (mes, setor, total) VALUES
  ('2026-01-01', 'RH', 18),
  ('2026-01-01', 'Marketing', 8),
  ('2026-01-01', 'Comercial', 5),
  ('2026-01-01', 'Dpto Químico', 4),
  ('2026-01-01', 'Produção', 3),
  ('2026-01-01', 'SIG', 3),
  ('2026-01-01', 'Compras', 1),
  ('2026-01-01', 'Representantes', 1),
  ('2026-02-01', 'RH', 15),
  ('2026-02-01', 'Marketing', 11),
  ('2026-02-01', 'Dpto Químico', 3),
  ('2026-02-01', 'Representantes', 2),
  ('2026-02-01', 'Brigadistas', 1),
  ('2026-02-01', 'CIPA', 1),
  ('2026-02-01', 'Comercial', 1),
  ('2026-02-01', 'Não informado', 1),
  ('2026-02-01', 'SIG', 1),
  ('2026-03-01', 'Marketing', 35),
  ('2026-03-01', 'RH', 16),
  ('2026-03-01', 'Comercial', 3),
  ('2026-03-01', 'Diretoria', 2),
  ('2026-03-01', 'Dpto Químico', 2),
  ('2026-03-01', 'SIG', 2),
  ('2026-03-01', 'CIPA', 1),
  ('2026-03-01', 'Produção', 1),
  ('2026-04-01', 'Marketing', 21),
  ('2026-04-01', 'RH', 16),
  ('2026-04-01', 'CIPA', 2),
  ('2026-04-01', 'Comercial', 2),
  ('2026-04-01', 'Diretoria', 2),
  ('2026-04-01', 'Dpto Químico', 1),
  ('2026-04-01', 'Gerentes de vendas', 1),
  ('2026-04-01', 'Não informado', 1),
  ('2026-04-01', 'SIG', 1),
  ('2026-04-01', 'Supervisão de Vendas', 1),
  ('2026-05-01', 'RH', 20),
  ('2026-05-01', 'Marketing', 19),
  ('2026-05-01', 'Comercial', 3),
  ('2026-05-01', 'Dpto Químico', 2),
  ('2026-05-01', 'Brigadistas', 1),
  ('2026-05-01', 'Controladoria', 1),
  ('2026-05-01', 'Diretoria', 1),
  ('2026-05-01', 'Financeiro', 1),
  ('2026-05-01', 'SIG', 1),
  ('2026-06-01', 'Marketing', 19),
  ('2026-06-01', 'RH', 11),
  ('2026-06-01', 'Dpto Químico', 2),
  ('2026-06-01', 'Controladoria', 1),
  ('2026-06-01', 'Financeiro', 1),
  ('2026-06-01', 'Produção', 1)
ON CONFLICT (mes, setor) DO UPDATE SET total = EXCLUDED.total;

COMMIT;

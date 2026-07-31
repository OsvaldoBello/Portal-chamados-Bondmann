-- 0069_marketing_volume_baseline.sql
-- Pedido do usuário (2026-07-31): o Dashboard de Marketing mostrava um "gap
-- absurdo" em ABR/26 e MAI/26 (e números baixos demais em jan-mar/26) porque
-- `vw_marketing_volume_mensal` só enxerga `chamados` — e o Marketing só
-- começou a abrir chamado de verdade pelo Portal a partir de julho/26 (os
-- primeiros tickets reais, `BOND-2026-00015` em diante, são de 6/jul). Os
-- meses anteriores foram controlados FORA do sistema; o usuário anexou o
-- dashboard estático que o time usava então ("Dashboard Marketing - Jan a Mai
-- (1).html") com os números reais desse período.
--
-- Esta tabela guarda esse HISTÓRICO PRÉ-SISTEMA como dado agregado mensal —
-- explicitamente NÃO como chamados individuais fabricados (não temos os
-- tickets originais, só o agregado; inventar 43 chamados falsos pra janeiro
-- seria fabricar dado, não corrigir). `AdminRepo.mkt_dashboard_data` usa esta
-- tabela pra SUBSTITUIR o cálculo da view nos meses em que ela tem linha —
-- ver a query e o comentário lá.
--
-- Mesmo padrão de RLS de `marketing_midia_regional` (0035/0068): leitura
-- livre pra autenticado, escrita só TI ou ADMIN/OPERADOR do próprio Marketing.

BEGIN;

CREATE TABLE IF NOT EXISTS marketing_volume_baseline (
  mes date PRIMARY KEY,
  total integer NOT NULL DEFAULT 0,
  concluidas integer NOT NULL DEFAULT 0,
  em_andamento integer NOT NULL DEFAULT 0,
  abertas integer NOT NULL DEFAULT 0,
  volume integer NOT NULL DEFAULT 0,
  mkt_orig integer NOT NULL DEFAULT 0,
  sol_orig integer NOT NULL DEFAULT 0,
  tempo_medio numeric(6, 1),
  atrasos integer NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE marketing_volume_baseline ENABLE ROW LEVEL SECURITY;

CREATE POLICY marketing_volume_baseline_select ON marketing_volume_baseline
  FOR SELECT TO authenticated USING (true);

CREATE POLICY marketing_volume_baseline_admin ON marketing_volume_baseline
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

REVOKE ALL ON marketing_volume_baseline FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON marketing_volume_baseline TO authenticated;

-- Fonte: "Dashboard Marketing - Jan a Mai (1).html" (anexado pelo usuário,
-- 2026-07-31) — números que o time de Marketing já usava antes do Portal.
INSERT INTO marketing_volume_baseline
  (mes, total, concluidas, em_andamento, abertas, volume, mkt_orig, sol_orig, tempo_medio, atrasos)
VALUES
  ('2026-01-01', 43, 39, 2, 2, 66, 15, 28, 1.8, 2),
  ('2026-02-01', 36, 33, 3, 0, 63, 11, 25, 3.0, 3),
  ('2026-03-01', 62, 57, 2, 3, 120, 34, 28, 0.8, 4),
  ('2026-04-01', 47, 46, 1, 0, 84, 21, 26, 3.0, 5),
  ('2026-05-01', 49, 42, 1, 6, 84, 19, 30, 4.0, 8)
ON CONFLICT (mes) DO NOTHING;

COMMIT;

-- 0073_marketing_baseline_junho.sql
-- Pedido do usuário (2026-08-03): junho/26 entra no Dashboard de Marketing.
-- Mesma situação de jan-mai (ver `0069_marketing_volume_baseline.sql`): o
-- Marketing só passou a abrir chamado de verdade pelo Portal em julho/26, então
-- junho é HISTÓRICO PRÉ-SISTEMA e entra como agregado mensal na
-- `marketing_volume_baseline`, não como chamados individuais fabricados.
--
-- Fonte: planilha "Indicadores (2).xlsx" anexada pelo usuário (2026-08-03),
-- aba `DemandasJUN` (35 linhas de demanda) e aba `MidiaCompartilhada`.
-- Os números abaixo foram agregados da aba com o MESMO critério que reproduz
-- exatamente as linhas de jan/mar/mai já em produção (conferido linha a linha
-- antes de escrever esta migration):
--   total        = nº de linhas da aba do mês
--   concluidas   = Status "Concluída"
--   em_andamento = Status "Em andamento"      → 2
--   abertas      = Status "Aberta"            → nenhuma em junho
--   volume       = soma da coluna "Volume"    → 213
--   mkt_orig     = "Origem da Demanda" = Marketing
--   sol_orig     = "Origem da Demanda" = Solicitação
--   tempo_medio  = média de "Tempo de entrega" das concluídas (4,545… → 4,5)
--   atrasos      = linhas com "Tempo de entrega" > 5 dias
--
-- ⚠️ O volume de junho (213) é MUITO acima da média do semestre (63-120) por
-- causa de dois itens de tiragem grande, não por um erro de digitação:
-- "Figurinhas/Album Copa do Mundo BD" (113 peças) e "Enviar reports Mídia -
-- Representantes" (45). O gráfico de Volume vai mostrar esse pico — é dado
-- real da planilha.
--
-- ⚠️ Divergências da planilha nova vs. o dashboard estático que originou a
-- `0069` (NÃO corrigidas aqui — só junho foi pedido; ver docs/CHANGELOG.md):
--   FEV/26 — tempo_medio 3,0 (0069) vs 1,5 (planilha); atrasos 3 vs 1.
--   ABR/26 — total 47 (0069) vs 48 (planilha); volume 84 vs 85; mkt_orig 21
--            vs 22; abertas 0 vs 1.
--
-- A quebra por setor solicitante ("4. Solicitantes") continua vindo só de
-- `vw_marketing_setor_mensal` (chamados reais) — junho fica sem essa quebra,
-- exatamente como jan-mai. A baseline guarda só o agregado.

BEGIN;

INSERT INTO marketing_volume_baseline
  (mes, total, concluidas, em_andamento, abertas, volume, mkt_orig, sol_orig, tempo_medio, atrasos)
VALUES
  ('2026-06-01', 35, 33, 2, 0, 213, 18, 17, 4.5, 7)
ON CONFLICT (mes) DO UPDATE SET
  total        = EXCLUDED.total,
  concluidas   = EXCLUDED.concluidas,
  em_andamento = EXCLUDED.em_andamento,
  abertas      = EXCLUDED.abertas,
  volume       = EXCLUDED.volume,
  mkt_orig     = EXCLUDED.mkt_orig,
  sol_orig     = EXCLUDED.sol_orig,
  tempo_medio  = EXCLUDED.tempo_medio,
  atrasos      = EXCLUDED.atrasos;

-- Mídia Regional de junho (aba `MidiaCompartilhada` da mesma planilha) —
-- a tabela já tem jan-mai; junho estava faltando. `DO UPDATE` (e não
-- `DO NOTHING`) porque o upload da planilha da agência sobrescreve o mês por
-- design (ver `app/services/ingestao_marketing_midia.py`) — reaplicar esta
-- migration não pode deixar um valor velho para trás.
INSERT INTO marketing_midia_regional
  (mes, investimento, regioes, descontinuidades, aderencias)
VALUES
  ('2026-06-01', 4233.41, 45, 1, 3)
ON CONFLICT (mes) DO UPDATE SET
  investimento     = EXCLUDED.investimento,
  regioes          = EXCLUDED.regioes,
  descontinuidades = EXCLUDED.descontinuidades,
  aderencias       = EXCLUDED.aderencias;

COMMIT;

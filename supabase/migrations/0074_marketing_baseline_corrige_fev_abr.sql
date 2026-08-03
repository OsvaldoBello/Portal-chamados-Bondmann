-- 0074_marketing_baseline_corrige_fev_abr.sql
-- Pedido do usuário (2026-08-03): corrigir as divergências que apareceram ao
-- comparar a planilha "Indicadores (2).xlsx" (anexada em 2026-08-03, a fonte
-- mais recente do time) com o que a `0069` gravou a partir do dashboard
-- estático "Dashboard Marketing - Jan a Mai (1).html" (2026-07-31).
--
-- A planilha é a fonte de verdade: é o controle que o Marketing mantém, e o
-- HTML era um retrato exportado dele num momento anterior. Os números abaixo
-- saem da mesma agregação usada na `0073` para junho (conferida reproduzindo
-- exatamente jan/mar/mai, que NÃO divergem e por isso não são tocados aqui).
--
--   FEV/26 — tempo_medio 3,0 → 1,5 e atrasos 3 → 1.
--            O resto da linha (36/33/3/0, volume 63, 11 mkt / 25 sol) já batia.
--            A média é sobre as 32 concluídas com "Tempo de entrega"
--            preenchido (das 33 concluídas do mês).
--
--   ABR/26 — total 47 → 48, abertas 0 → 1, volume 84 → 85, mkt_orig 21 → 22.
--            Uma demanda inteira faltava na linha da `0069`: aparece na aba
--            `DemandasABR` como Aberta, de origem Marketing, volume 1 — o que
--            explica os quatro campos se movendo juntos. concluidas (46),
--            em_andamento (1), sol_orig (26), tempo_medio (3,04 → 3,0) e
--            atrasos (5) continuam iguais.
--
-- Efeito no dashboard: o acumulado de demandas sobe de 272 para 273 e o de
-- volume de 630 para 631 no recorte jan-jun; o gráfico de tempo médio deixa de
-- mostrar um pico falso em fevereiro (3,0 d contra 1,5 d real).

BEGIN;

UPDATE marketing_volume_baseline
   SET tempo_medio = 1.5,
       atrasos     = 1
 WHERE mes = '2026-02-01';

UPDATE marketing_volume_baseline
   SET total    = 48,
       abertas  = 1,
       volume   = 85,
       mkt_orig = 22
 WHERE mes = '2026-04-01';

COMMIT;

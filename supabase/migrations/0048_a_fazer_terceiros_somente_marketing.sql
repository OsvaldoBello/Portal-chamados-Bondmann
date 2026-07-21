-- 0048_a_fazer_terceiros_somente_marketing.sql
-- Decisão de produto (2026-07-21): os status "A fazer" (A_FAZER) e "Aguardando
-- terceiros" (AGUARDANDO_TERCEIROS) voltam a ser exclusivos do Marketing (quadro
-- Trello com autoatendimento/validação). Os demais setores (TI, RH, etc.) usam o
-- fluxo clássico NOVO -> EM_ATENDIMENTO -> AGUARDANDO -> RESOLVIDO. A UI (dropdown
-- de status, colunas do Kanban) já deixa de oferecer esses dois status fora do
-- Marketing (app/routes/workspace.py, _status_ui); aqui migramos o dado residual
-- para não deixar chamado preso num status que a UI não oferece mais.
--
-- Contexto: a migration 0047 generalizou o autoatendimento para todos os setores
-- e a 023598c passou a exibir A_FAZER no Kanban de todos, para os 471 chamados
-- "[Legado #...]" importados como A_FAZER. O time já moveu esses legados para NOVO
-- manualmente; este UPDATE cobre qualquer resíduo e é idempotente.
--
-- Marketing é preservado (mantém A_FAZER/AGUARDANDO_TERCEIROS). O enum
-- status_chamado NÃO é alterado — os valores continuam válidos no banco, apenas
-- deixam de ser oferecidos fora do Marketing.

-- "A fazer" fora do Marketing -> "Novo" (início da fila clássica).
UPDATE chamados
   SET status = 'NOVO'
 WHERE status = 'A_FAZER'
   AND departamento_id IS DISTINCT FROM (SELECT id FROM departamentos WHERE nome = 'Marketing');

-- "Aguardando terceiros" fora do Marketing -> "Aguardando" (mesma semântica de
-- "pausado" no fluxo clássico; a trigger sla_pausa_aguardando trata os dois igual).
UPDATE chamados
   SET status = 'AGUARDANDO'
 WHERE status = 'AGUARDANDO_TERCEIROS'
   AND departamento_id IS DISTINCT FROM (SELECT id FROM departamentos WHERE nome = 'Marketing');

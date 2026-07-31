-- 0071_backfill_origem_demanda_por_etiqueta_setor.sql
-- Correção da 0070, no mesmo dia: o usuário esclareceu que "Origem da
-- Demanda" deve seguir a ETIQUETA "setor" do próprio chamado (a mesma badge
-- exibida no cartão do Kanban, `c.setor`) — não o departamento real de quem
-- abriu (regra da 0070, que já estava errada: dava 60 chamados como
-- "Marketing" em julho/26; pela etiqueta certa são 52).
--
-- "para marketing tem que puxar o número de chamados com a etiqueta
-- MARKETing, as demais etiquetas devem contar para solicitações" — pedido
-- do usuário, 2026-07-31. `app/routes/portal.py::criar_chamado` já foi
-- ajustado pra decidir por `setor` na abertura; esta migration reaplica a
-- regra certa aos chamados do Marketing que já existem (substitui o efeito
-- da 0070 por completo, não é cumulativa com ela).

BEGIN;

UPDATE chamados c
   SET origem_demanda = CASE
         WHEN lower(btrim(c.setor)) = 'marketing' THEN 'Marketing'
         ELSE 'Solicitação'
       END
  FROM departamentos dep
 WHERE dep.id = c.departamento_id
   AND dep.nome = 'Marketing';

COMMIT;

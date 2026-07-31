-- 0070_backfill_origem_demanda_marketing.sql
-- Pedido do usuário (2026-07-31): "quando um operador de marketing, o felipe
-- por exemplo, abre um chamado, é preciso que ele seja contado como
-- marketing, solicitações são referentes a outros setores abrirem chamados".
--
-- `app/routes/portal.py::criar_chamado` passou a decidir `origem_demanda` na
-- abertura pelo departamento do AUTOR (mesmo setor do destino = "Marketing";
-- setor diferente = "Solicitação") em vez de sempre gravar "Solicitação" e
-- depender de reclassificação manual na tela de atendimento — quase nenhum
-- chamado real estava sendo reclassificado (76 chamados de julho/26, só 1
-- como "Marketing" antes deste backfill).
--
-- Esta migration aplica a MESMA regra nova aos chamados do Marketing que já
-- existem, pra o dashboard não continuar refletindo a regra antiga no
-- histórico. Só toca chamados cujo destino é Marketing E cujo autor tem
-- perfil (join, não LEFT JOIN — sem perfil não há como decidir, a linha fica
-- como estava).

BEGIN;

UPDATE chamados c
   SET origem_demanda = CASE
         WHEN autor.departamento_id = c.departamento_id THEN 'Marketing'
         ELSE 'Solicitação'
       END
  FROM departamentos dep, perfis autor
 WHERE dep.id = c.departamento_id
   AND dep.nome = 'Marketing'
   AND autor.id = c.cliente_id;

COMMIT;

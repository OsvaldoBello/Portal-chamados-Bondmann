-- 0042_rh_autoatendimento.sql
-- Decisão de produto 2026-07-13 (reunião): o RH vai, assim como o Marketing,
-- abrir e gerenciar chamados para o PRÓPRIO setor (ex.: iniciativas internas de
-- RH). A exceção de segregação de função das migrations 0029/0038 só existia
-- para "Marketing" (nome do departamento hardcoded) — generaliza isso para uma
-- coluna `autoatendimento` em `departamentos`, marcada para Marketing e RH.
--
-- Sem o Kanban de 5 colunas do Marketing (coluna "A Fazer"): isso é só a
-- exceção de segregação de função (autor pode assumir e responder o próprio
-- chamado) + visibilidade na fila/Kanban — não o quadro Trello inteiro nem os
-- campos de demanda (data_entrega/volume/sem_prazo/origem_demanda), que
-- continuam exclusivos do Marketing.

BEGIN;

-- 1) Coluna que substitui o "dep.nome = 'Marketing'" hardcoded.
ALTER TABLE departamentos ADD COLUMN IF NOT EXISTS autoatendimento boolean NOT NULL DEFAULT false;
UPDATE departamentos SET autoatendimento = true WHERE nome IN ('Marketing', 'RH');

-- 2) Função auxiliar (mesmo padrão de auth_is_ti()) para reuso nas policies e,
--    futuramente, em queries de app sem precisar de mais um JOIN.
CREATE OR REPLACE FUNCTION public.auth_departamento_autoatendimento()
 RETURNS boolean
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'public'
AS $function$
  SELECT COALESCE(d.autoatendimento, false)
    FROM public.perfis p
    JOIN public.departamentos d ON d.id = p.departamento_id
   WHERE p.id = auth.uid();
$function$;

-- 3) UPDATE de chamados: operador_id só pode igualar cliente_id (autoatendimento)
--    quando o departamento de destino tem autoatendimento = true (era só Marketing).
DROP POLICY chamados_update_staff ON chamados;
CREATE POLICY chamados_update_staff ON chamados
  FOR UPDATE TO authenticated
  USING (
    auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id()
  )
  WITH CHECK (
    (
      operador_id IS NULL
      OR operador_id <> cliente_id
      OR EXISTS (SELECT 1 FROM departamentos d WHERE d.id = departamento_id AND d.autoatendimento)
    )
    AND (
      auth_is_ti()
      OR (auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id())
    )
  );

-- 4) INSERT de mensagens pelo staff: mesma generalização da 0038, agora por flag.
DROP POLICY mensagens_insert ON mensagens;
CREATE POLICY mensagens_insert ON mensagens
  FOR INSERT TO authenticated
  WITH CHECK (
    remetente_id = auth.uid()
    AND EXISTS (
      SELECT 1 FROM chamados c
      JOIN departamentos d ON d.id = c.departamento_id
      WHERE c.id = mensagens.chamado_id
        AND (
          (
            auth_departamento_id() IS NOT NULL
            AND c.departamento_id = auth_departamento_id()
            AND c.operador_id IS NOT NULL
            AND (c.cliente_id <> mensagens.remetente_id OR d.autoatendimento)
          )
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
        )
    )
  );

COMMIT;

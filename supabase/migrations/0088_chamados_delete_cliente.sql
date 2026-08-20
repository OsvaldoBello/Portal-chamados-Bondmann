-- 0088_chamados_delete_cliente.sql
-- Autor (CLIENTE) passa a poder excluir o PRÓPRIO chamado, mas só enquanto
-- ele ainda não foi atendido — decisão do gestor (2026-08-20), revertendo
-- parcialmente a `0025_chamados_delete_staff.sql` ("O funcionário (CLIENTE)
-- NUNCA apaga"). Motivo real: durante um teste do intake via WhatsApp,
-- chamados de teste abertos por engano só puderam ser removidos pelo staff
-- (RH/TI) — o autor não tinha como desfazer sozinho.
--
-- Escopo deliberadamente restrito a "aberto por engano, ninguém mexeu ainda":
--   status = 'NOVO'        — staff não mudou o status (reivindicar já muda).
--   operador_id IS NULL    — ninguém se atribuiu o chamado.
--   respondido_em IS NULL  — staff nunca respondeu publicamente (marcado por
--                            `responder_staff` na 1ª resposta pública,
--                            independente de status/operador_id).
-- As três condições cobrem tanto o fluxo de reivindicar (que muda status e
-- operador_id juntos) quanto uma resposta direta sem reivindicar antes. Uma
-- vez que qualquer uma delas deixa de valer, só staff do setor de destino
-- (ou TI) apaga — `chamados_delete_staff` já cobre isso, sem mudança aqui.
--
-- `mensagens`/`historico_chamados` continuam com `ON DELETE CASCADE` (Seção
-- 5.1 do plano mestre) — nada de policy adicional necessária nessas tabelas.

CREATE POLICY chamados_delete_cliente ON chamados
  FOR DELETE TO authenticated
  USING (
    cliente_id = auth.uid()
    AND status = 'NOVO'::status_chamado
    AND operador_id IS NULL
    AND respondido_em IS NULL
  );

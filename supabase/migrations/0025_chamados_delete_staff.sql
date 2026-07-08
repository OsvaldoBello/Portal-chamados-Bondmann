-- 0025_chamados_delete_staff.sql
-- Exclusão de chamados errados (feature): operadores/admins precisam poder
-- apagar um chamado aberto por engano. Mesma escopo de `chamados_update_staff`
-- (migration 0010): TI apaga qualquer chamado; RH/Marketing só os do próprio
-- setor. O funcionário (CLIENTE) NUNCA apaga (sem policy de DELETE para ele).
--
-- `mensagens` e `historico_chamados` têm FK `ON DELETE CASCADE` para
-- `chamados` (Seção 5.1 do plano mestre) — a exclusão do chamado já limpa o
-- histórico da conversa, sem policy adicional necessária nessas tabelas.

CREATE POLICY chamados_delete_staff ON chamados
  FOR DELETE TO authenticated
  USING (
    auth_is_ti()
    OR (auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id())
  );

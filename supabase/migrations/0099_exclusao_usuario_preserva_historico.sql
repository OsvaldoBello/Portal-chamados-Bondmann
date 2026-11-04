-- 0099_exclusao_usuario_preserva_historico.sql
-- Permite a exclusão de operadores e administradores mesmo que possuam chamados
-- já atendidos ou mensagens no histórico, preservando a autoria e rastreabilidade
-- dos atendimentos anteriores (auditoria, CSAT, relatórios e chat).
--
-- Contexto do problema:
--   1. Originalmente, `perfis.id` possuía FK `REFERENCES auth.users(id) ON DELETE CASCADE` (0002).
--   2. Ao excluir o usuário no GoTrue (auth.users), o PostgreSQL propagava CASCADE para `perfis`.
--   3. Ao tentar deletar `perfis`, disparava erro de violação de chave estrangeira em
--      `mensagens.remetente_id` (`REFERENCES perfis(id) ON DELETE RESTRICT`).
--   4. Além disso, `chamados.operador_id` e `historico_chamados.ator_id` possuíam `ON DELETE SET NULL`,
--      o que apagaria a identidade de quem atendeu e finalizou os chamados se o perfil fosse removido.
--
-- Solução:
--   1. Remove a constraint `perfis_id_fkey` com ON DELETE CASCADE entre `perfis` e `auth.users`.
--      Assim, a exclusão da conta em `auth.users` revoga permanentemente o login, sessões e credenciais
--      sem remover o registro histórico em `perfis`.
--   2. Trigger `on_auth_user_deleted` em `auth.users`:
--      - Marca `perfis.ativo = false`;
--      - Desatribui chamados em aberto (`status <> 'RESOLVIDO'`), liberando-os para a fila
--        (`operador_id = NULL`, e status `EM_ATENDIMENTO` retorna para `NOVO`);
--      - Mantém os chamados já finalizados (`status = 'RESOLVIDO'`) intactos com o `operador_id` original,
--        garantindo que o histórico informe quem de fato atendeu e concluiu a demanda.

-- 1. Remove o CASCADE automático de auth.users para perfis
ALTER TABLE public.perfis DROP CONSTRAINT IF EXISTS perfis_id_fkey;

-- 2. Trigger para quando o usuário for excluído de auth.users (GoTrue Admin API / painel)
CREATE OR REPLACE FUNCTION public.handle_user_deleted()
RETURNS trigger AS $$
BEGIN
  -- Desatribui chamados em aberto atribuídos a este operador
  UPDATE public.chamados
     SET operador_id = NULL,
         status = CASE WHEN status = 'EM_ATENDIMENTO' THEN 'NOVO'::status_chamado ELSE status END
   WHERE operador_id = OLD.id
     AND status <> 'RESOLVIDO';

  -- Marca o perfil como inativo
  UPDATE public.perfis
     SET ativo = false
   WHERE id = OLD.id;

  RETURN OLD;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_auth_user_deleted ON auth.users;
CREATE TRIGGER on_auth_user_deleted
  AFTER DELETE ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_user_deleted();

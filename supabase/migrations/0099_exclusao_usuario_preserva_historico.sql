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
--   2. A desativação e desatribuição de chamados em aberto é realizada pelo backend
--      (`AdminRepo.desativar_usuario`) antes de chamar a GoTrue Admin API. Não se deve
--      adicionar trigger em `auth.users` que tente mutar tabelas em `public`, pois o GoTrue
--      executa com o papel `supabase_auth_admin` que falha nessas operações ("database error deleting user").

-- 1. Remove o CASCADE automático de auth.users para perfis
ALTER TABLE public.perfis DROP CONSTRAINT IF EXISTS perfis_id_fkey;

-- 2. Garante que não haja trigger em auth.users mutando tabelas públicas
DROP TRIGGER IF EXISTS on_auth_user_deleted ON auth.users;
DROP FUNCTION IF EXISTS public.handle_user_deleted();

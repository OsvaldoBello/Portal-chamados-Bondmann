-- 0036_fix_recursao_rls_observadores.sql
-- Bug CRÍTICO (2026-07-09, corrigido no mesmo dia): quebrava o site inteiro,
-- local e no Railway, com 500 "Erro interno" em toda tela que lista/abre
-- chamados (Fila, Kanban, Meus chamados, Chamados do Departamento, Atendimento).
--
-- Causa: `chamados_select`/`mensagens_select` (0034, "em cópia") passaram a
-- checar "sou observador?" com um EXISTS DIRETO em `chamados_observadores`.
-- Essa tabela tem RLS própria (`chamados_observadores_select`/insert/delete)
-- cujas policies fazem o caminho INVERSO — um EXISTS em `chamados` — para
-- decidir se quem pergunta pode ver aquela linha de observação. As duas RLS
-- se chamando mutuamente faz o Postgres estourar em runtime:
--   asyncpg.exceptions.InvalidObjectDefinitionError:
--     infinite recursion detected in policy for relation "chamados"
--
-- Fix (mesmo padrão já usado por auth_is_ti()/auth_departamento_id() para
-- evitar recursão ao ler `perfis`): função SECURITY DEFINER que lê
-- `chamados_observadores` SEM reaplicar a RLS dela. Como `chamados_select`/
-- `mensagens_select` passam a chamar essa função em vez de fazer o EXISTS
-- direto na tabela, o ciclo se rompe (a função, ao rodar como o dono/definer,
-- não re-decide via `chamados_observadores_select`, que é o outro lado do laço).

BEGIN;

CREATE OR REPLACE FUNCTION auth_eh_observador(p_chamado_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE SECURITY DEFINER
SET search_path TO 'public'
AS $$
  SELECT EXISTS (
    SELECT 1 FROM chamados_observadores
    WHERE chamado_id = p_chamado_id AND perfil_id = auth.uid()
  );
$$;

REVOKE ALL ON FUNCTION auth_eh_observador(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth_eh_observador(uuid) TO authenticated;

DROP POLICY chamados_select ON chamados;
CREATE POLICY chamados_select ON chamados
  FOR SELECT TO authenticated
  USING (
    cliente_id = auth.uid()
    OR (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
    OR (auth_role() = ANY (ARRAY['OPERADOR','ADMIN']::papel_usuario[]) AND departamento_id = auth_departamento_id())
    OR (
      auth_role() = 'ADMIN' AND auth_departamento_id() IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM perfis autor
        WHERE autor.id = chamados.cliente_id
          AND autor.departamento_id = auth_departamento_id()
      )
    )
    OR auth_eh_observador(chamados.id)
  );

DROP POLICY mensagens_select ON mensagens;
CREATE POLICY mensagens_select ON mensagens
  FOR SELECT TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = mensagens.chamado_id
        AND (
          (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
          OR (auth_role() = ANY (ARRAY['OPERADOR','ADMIN']::papel_usuario[]) AND c.departamento_id = auth_departamento_id())
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
          OR (
            auth_role() = 'ADMIN' AND auth_departamento_id() IS NOT NULL
            AND mensagens.is_interna = false
            AND EXISTS (
              SELECT 1 FROM perfis autor
              WHERE autor.id = c.cliente_id
                AND autor.departamento_id = auth_departamento_id()
            )
          )
          OR (mensagens.is_interna = false AND auth_eh_observador(c.id))
        )
    )
  );

COMMIT;

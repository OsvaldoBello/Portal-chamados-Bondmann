-- 0028_lideranca_setor.sql
-- Toda conta (inclusive Funcionário/CLIENTE) passa a ter um departamento_id —
-- o setor a que a pessoa pertence, não só o setor que ela ATENDE. Isso permite
-- um "líder de setor" (role ADMIN + departamento_id) ACOMPANHAR todos os
-- chamados abertos pela sua equipe, mesmo num departamento que só ABRE chamado
-- (não tem fila/staff — ex.: Comercial, Financeiro).
--
-- Consequências:
--   1. Remove o guarda-corpo de perfis.departamento_id que exigia recebe_chamados
--      (0027) — agora QUALQUER departamento ativo é um setor válido de origem
--      para um usuário. O guarda-corpo em chamados.departamento_id (destino do
--      chamado) continua valendo: só um setor com fila pode ser DESTINO.
--   2. RLS: ADMIN com departamento_id passa a enxergar (SELECT) os chamados cujo
--      AUTOR pertence ao mesmo departamento_id — além do que já enxergava (fila
--      do próprio setor, se houver). É só leitura: as policies de UPDATE/INSERT
--      de mensagem continuam exigindo que o chamado seja da fila do setor
--      (departamento_id do CHAMADO = auth_departamento_id()), então o líder não
--      "atende" chamados de fora da própria fila, só acompanha.

BEGIN;

DROP TRIGGER IF EXISTS trg_perfis_departamento_recebe ON perfis;

DROP POLICY chamados_select ON chamados;
CREATE POLICY chamados_select ON chamados
  FOR SELECT TO authenticated
  USING (
    cliente_id = auth.uid()
    OR (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
    OR (auth_role() IN ('OPERADOR','ADMIN') AND departamento_id = auth_departamento_id())
    OR (
      auth_role() = 'ADMIN' AND auth_departamento_id() IS NOT NULL
      AND EXISTS (
        SELECT 1 FROM perfis autor
         WHERE autor.id = chamados.cliente_id
           AND autor.departamento_id = auth_departamento_id()
      )
    )
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
          OR (auth_role() IN ('OPERADOR','ADMIN') AND c.departamento_id = auth_departamento_id())
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
          OR (
            auth_role() = 'ADMIN' AND auth_departamento_id() IS NOT NULL
            AND mensagens.is_interna = false
            AND EXISTS (
              SELECT 1 FROM perfis autor
               WHERE autor.id = c.cliente_id AND autor.departamento_id = auth_departamento_id()
            )
          )
        )
    )
  );

DROP POLICY historico_select ON historico_chamados;
CREATE POLICY historico_select ON historico_chamados
  FOR SELECT TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = historico_chamados.chamado_id
        AND (
          c.cliente_id = auth.uid()
          OR (auth_role() = 'ADMIN' AND auth_departamento_id() IS NULL)
          OR (auth_role() IN ('OPERADOR','ADMIN') AND c.departamento_id = auth_departamento_id())
          OR (
            auth_role() = 'ADMIN' AND auth_departamento_id() IS NOT NULL
            AND EXISTS (
              SELECT 1 FROM perfis autor
               WHERE autor.id = c.cliente_id AND autor.departamento_id = auth_departamento_id()
            )
          )
        )
    )
  );

COMMIT;

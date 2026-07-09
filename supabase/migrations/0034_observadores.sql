-- 0034_observadores.sql
-- "Em cópia" / observadores multi-setoriais (Fase 8 — 2026-07-09): permite
-- adicionar outro usuário (de qualquer setor) para ACOMPANHAR um chamado —
-- leitura do chamado + mensagens públicas, recebe notificação (sino/Realtime,
-- já assinam a tabela `chamados`/`mensagens`) — sem poder responder/alterar
-- (mantém o modelo de responsabilidade: quem atende é sempre o setor de
-- destino, mesma decisão da Fase 0).

BEGIN;

CREATE TABLE IF NOT EXISTS chamados_observadores (
  chamado_id  uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  perfil_id   uuid NOT NULL REFERENCES perfis(id) ON DELETE CASCADE,
  criado_por  uuid REFERENCES perfis(id) ON DELETE SET NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chamado_id, perfil_id)
);
CREATE INDEX idx_chamados_observadores_perfil ON chamados_observadores(perfil_id);

ALTER TABLE chamados_observadores ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, DELETE ON chamados_observadores TO authenticated;

-- SELECT: quem já enxerga o chamado (autor OU staff do setor de destino) vê a
-- lista de observadores; o próprio observador também vê que está na cópia.
CREATE POLICY chamados_observadores_select ON chamados_observadores
  FOR SELECT TO authenticated
  USING (
    perfil_id = auth.uid()
    OR EXISTS (
      SELECT 1 FROM chamados c
       WHERE c.id = chamados_observadores.chamado_id
         AND (
           c.cliente_id = auth.uid()
           OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
         )
    )
  );

-- INSERT/DELETE: só quem já enxerga o chamado (autor ou staff do setor) pode
-- adicionar/remover um observador — "só quem já pode ver o chamado pode
-- adicionar observadores" (decisão de produto).
CREATE POLICY chamados_observadores_insert ON chamados_observadores
  FOR INSERT TO authenticated
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM chamados c
       WHERE c.id = chamados_observadores.chamado_id
         AND (
           c.cliente_id = auth.uid()
           OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
         )
    )
  );

CREATE POLICY chamados_observadores_delete ON chamados_observadores
  FOR DELETE TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM chamados c
       WHERE c.id = chamados_observadores.chamado_id
         AND (
           c.cliente_id = auth.uid()
           OR (auth_departamento_id() IS NOT NULL AND c.departamento_id = auth_departamento_id())
         )
    )
  );

-- Observador ganha visibilidade de LEITURA em `chamados`/`mensagens` (só
-- mensagens públicas — nunca nota interna). Reescreve as policies de SELECT
-- (idênticas à 0028, só acrescentando o ramo do observador).
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
    OR EXISTS (
      SELECT 1 FROM chamados_observadores o
       WHERE o.chamado_id = chamados.id AND o.perfil_id = auth.uid()
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
          OR (
            mensagens.is_interna = false
            AND EXISTS (
              SELECT 1 FROM chamados_observadores o
               WHERE o.chamado_id = c.id AND o.perfil_id = auth.uid()
            )
          )
        )
    )
  );

COMMIT;

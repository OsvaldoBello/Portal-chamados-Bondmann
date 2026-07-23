-- 0050_ia_triagens.sql — Frente de IA de triagem, F0 (plano_md_mestre_IA.md, Seção 4.1)
--
-- Auditoria e idempotência do motor de triagem por IA (agentes TI e Químico).
-- Cada rodada/passe de triagem grava UMA linha aqui: ação tomada, saída
-- estruturada (sem dados sigilosos — ver nota abaixo), modelo, tokens, custo e
-- duração. O UNIQUE (chamado_id, rodada, passe) materializa no banco duas
-- garantias do plano: retry não duplica triagem e o teto de rodadas é
-- verificável (Regra de Ouro #3 / Seção 2.3).
--
-- `resultado` do Passe B (Químico) guarda APENAS estrutura/metadados (ids de
-- semelhantes, produto identificado, flags) — nunca o texto integral da
-- pré-análise, que vive em `mensagens` com is_interna=true (RLS já validada).
-- Evita criar um segundo lugar sensível a proteger (decisão da Seção 4.1).
--
-- RLS: habilitado (o event trigger `ensure_rls` da 0046 já garantiria, mas o
-- ENABLE é explícito — lição da 0045). Leitura: só staff (OPERADOR/ADMIN) no
-- escopo do departamento de DESTINO do chamado — mesmo escopo de
-- `historico_chamados`. O AUTOR do chamado nunca lê (a triagem é material
-- interno de atendimento). Escrita: NENHUMA policy — INSERT/UPDATE acontecem
-- exclusivamente pela conexão administrativa (app/db.py::admin_connection),
-- que herda o papel da DSN e não passa por `authenticated`.
--
-- Idempotente (reexecução não duplica).

BEGIN;

CREATE TABLE IF NOT EXISTS ia_triagens (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id     uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  rodada         smallint NOT NULL CHECK (rodada >= 1),
  passe          text NOT NULL CHECK (passe IN ('UNICO', 'A', 'B')),
  acao           text NOT NULL CHECK (acao IN ('NOTA_INTERNA', 'PERGUNTAS', 'SEM_ACAO', 'ERRO')),
  resultado      jsonb NOT NULL DEFAULT '{}'::jsonb,
  modelo         text NOT NULL,
  tokens_entrada integer,
  tokens_saida   integer,
  custo_usd      numeric(10, 6),
  duracao_ms     integer,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (chamado_id, rodada, passe)
);

COMMENT ON TABLE ia_triagens IS
  'Auditoria por rodada/passe da triagem de chamados por IA (plano_md_mestre_IA.md, Seção 4.1). Escrita só via admin_connection(); leitura só staff do departamento do chamado.';
COMMENT ON COLUMN ia_triagens.resultado IS
  'Saída estruturada validada do passe. No Passe B (Químico): apenas metadados/ids — nunca texto com dado sigiloso (o texto vive em mensagens.is_interna=true).';

CREATE INDEX IF NOT EXISTS idx_ia_triagens_chamado ON ia_triagens(chamado_id);

ALTER TABLE ia_triagens ENABLE ROW LEVEL SECURITY;

-- Leitura: staff do departamento de destino do chamado (mesmo escopo de
-- historico_chamados pós-0020: nenhum tratamento especial para TI).
DROP POLICY IF EXISTS ia_triagens_select_staff ON ia_triagens;
CREATE POLICY ia_triagens_select_staff ON ia_triagens
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = ia_triagens.chamado_id
        AND auth_role() IN ('OPERADOR', 'ADMIN')
        AND c.departamento_id = auth_departamento_id()
    )
  );

-- Sem policies de INSERT/UPDATE/DELETE: escrita exclusiva da conexão
-- administrativa (fora do role `authenticated`) — intencional.

COMMIT;

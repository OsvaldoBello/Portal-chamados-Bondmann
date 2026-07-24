-- 0054 — Base de conhecimento sigilosa do Dpto Químico + role `ia_worker` (F4).
--
-- plano_md_mestre_IA.md, Seções 3.3 / 4.3 / C7. A base que hoje vive em
-- planilha + PDF anexados a um GPT (arranjo menos seguro) passa a morar SÓ no
-- banco — nunca no repositório. A ingestão é feita por script local
-- re-executável (`scripts/ingestao_base_quimico.py`); nenhuma rota HTTP expõe
-- estas tabelas.
--
-- Modelo de segurança (defesa em profundidade):
--   1. RLS habilitado em TODAS as tabelas; nenhuma policy para
--      anon/authenticated ⇒ inacessíveis via PostgREST/claims de usuário.
--   2. Role dedicado `ia_worker` (o Passe B lê a base por uma conexão própria):
--      GRANT de SELECT + policy `TO ia_worker` apenas nas tabelas permitidas.
--      (Policy é necessária porque RLS também se aplica ao `ia_worker` — só
--      owner/superuser passam por cima; correção registrada na Seção 4.3.)
--   3. `base_quimico_formulacoes` (quantidades — a formulação de fato): SEM
--      GRANT e SEM policy para `ia_worker` ⇒ erro de permissão no banco.
--      As quantidades não entram em contexto de modelo algum (Regra de Ouro #4).
--
-- A senha do role é definida FORA da migration (painel/SQL editor — ver
-- docs/runbook_hardening_gestor.md); aqui o role nasce com LOGIN mas sem senha
-- (login impossível até o gestor definir uma).
--
-- Idempotente: pode rodar mais de uma vez sem erro.

-- ---------------------------------------------------------------------------
-- 1) Tabelas (colunas conferidas contra a planilha real em 2026-07-23 —
--    "as abas mandam", Seção 4.3)
-- ---------------------------------------------------------------------------

-- Catálogo de produtos (aba Base_IA_Produtos: 61 linhas).
-- `componentes` = NOMES de matérias-primas, SEM proporção/quantidade.
CREATE TABLE IF NOT EXISTS base_quimico_produtos (
  chave_produto    text PRIMARY KEY,          -- ex.: 'BD AGRO|S1064|ALKARES|L4|001'
  segmento         text,
  codigo_produto   text,
  nome             text NOT NULL,             -- casa com o select "Produto" do FB033
  nome_normalizado text,
  aplicacao        text,                      -- "Descrição / aplicação"
  familia_tecnica  text,
  tipo_uso         text,
  componentes      jsonb NOT NULL DEFAULT '[]'::jsonb,  -- nomes, SEM quantidades
  palavras_chave   text[],
  orientacao       text,                      -- "Orientação para resposta da IA"
  atualizado_em    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_base_quimico_produtos_nome ON base_quimico_produtos (nome);

-- Matérias-primas (aba Base_IA_Materias_Primas: 94 linhas).
CREATE TABLE IF NOT EXISTS base_quimico_materias_primas (
  codigo_mp       text PRIMARY KEY,           -- ex.: 'M001'
  nome            text NOT NULL,              -- "Nome classificado"
  formula_quimica text,
  utilizacoes     text,                       -- "Principais utilizações"
  atualizado_em   timestamptz NOT NULL DEFAULT now()
);

-- Playbooks de diagnóstico/perguntas/regras (abas Diagnostico_Ocorrencias,
-- Perguntas_Investigacao e Regras_Sigilo_Resposta — pequenas e vivas).
-- `tipo` define o consumidor: PERGUNTA_INVESTIGACAO alimenta o Passe A (sem
-- dado de produto); DIAGNOSTICO e REGRA_SIGILO alimentam o Passe B.
CREATE TABLE IF NOT EXISTS base_quimico_playbooks (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tipo          text NOT NULL CHECK (tipo IN ('DIAGNOSTICO', 'PERGUNTA_INVESTIGACAO', 'REGRA_SIGILO')),
  sintoma       text NOT NULL,                -- sintoma / cenário / tipo de informação
  dados         jsonb NOT NULL DEFAULT '{}'::jsonb,  -- demais colunas da aba, rotuladas
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tipo, sintoma)                      -- chave natural do upsert da ingestão
);

-- Fichas técnicas fatiadas por produto (PDF ~71 páginas → texto por produto).
CREATE TABLE IF NOT EXISTS base_quimico_fichas (
  chave_produto text PRIMARY KEY REFERENCES base_quimico_produtos(chave_produto) ON DELETE CASCADE,
  conteudo      text NOT NULL,                -- texto integral da ficha do produto
  atualizado_em timestamptz NOT NULL DEFAULT now()
);

-- A formulação de fato — quantidades (aba Base_IA_Componentes, nível de
-- sigilo "Confidencial / formulação"). NENHUM passe de modelo acessa (C7).
CREATE TABLE IF NOT EXISTS base_quimico_formulacoes (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chave_produto text NOT NULL REFERENCES base_quimico_produtos(chave_produto) ON DELETE CASCADE,
  ordem         smallint NOT NULL,
  codigo_mp     text,
  componente    text NOT NULL,
  quantidade    numeric,
  funcao        text,
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (chave_produto, ordem)               -- chave natural do upsert da ingestão
);

-- ---------------------------------------------------------------------------
-- 2) RLS: habilitado em todas; NENHUMA policy para papéis de usuário
--    (anon/authenticated ficam sem acesso via PostgREST/claims).
-- ---------------------------------------------------------------------------

ALTER TABLE base_quimico_produtos        ENABLE ROW LEVEL SECURITY;
ALTER TABLE base_quimico_materias_primas ENABLE ROW LEVEL SECURITY;
ALTER TABLE base_quimico_playbooks       ENABLE ROW LEVEL SECURITY;
ALTER TABLE base_quimico_fichas          ENABLE ROW LEVEL SECURITY;
ALTER TABLE base_quimico_formulacoes     ENABLE ROW LEVEL SECURITY;

-- ---------------------------------------------------------------------------
-- 3) Role `ia_worker` (C7): SELECT só no que o Passe B pode ler.
--    Formulações: sem GRANT e sem policy — intencional e testado (Seção 8.2).
-- ---------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ia_worker') THEN
    CREATE ROLE ia_worker LOGIN;  -- senha definida fora da migration (runbook)
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO ia_worker;
GRANT SELECT ON base_quimico_produtos,
                base_quimico_materias_primas,
                base_quimico_playbooks,
                base_quimico_fichas TO ia_worker;
-- SEM grant em base_quimico_formulacoes — intencional (C7).
REVOKE ALL ON base_quimico_formulacoes FROM ia_worker;

-- Policies de leitura restritas AO role ia_worker (RLS vale para ele também).
DROP POLICY IF EXISTS bq_produtos_ia_worker ON base_quimico_produtos;
CREATE POLICY bq_produtos_ia_worker ON base_quimico_produtos
  FOR SELECT TO ia_worker USING (true);

DROP POLICY IF EXISTS bq_mps_ia_worker ON base_quimico_materias_primas;
CREATE POLICY bq_mps_ia_worker ON base_quimico_materias_primas
  FOR SELECT TO ia_worker USING (true);

DROP POLICY IF EXISTS bq_playbooks_ia_worker ON base_quimico_playbooks;
CREATE POLICY bq_playbooks_ia_worker ON base_quimico_playbooks
  FOR SELECT TO ia_worker USING (true);

DROP POLICY IF EXISTS bq_fichas_ia_worker ON base_quimico_fichas;
CREATE POLICY bq_fichas_ia_worker ON base_quimico_fichas
  FOR SELECT TO ia_worker USING (true);

-- base_quimico_formulacoes: ZERO policies (nem ia_worker, nem usuários).

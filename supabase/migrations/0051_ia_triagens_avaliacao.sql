-- 0051_ia_triagens_avaliacao.sql — Avaliação da nota interna da IA (plano_md_mestre_IA.md, Seção 10.2)
--
-- O KPI "notas internas avaliadas úteis pelos atendentes ≥ 70%" precisa de uma
-- fonte de dados: o atendente avalia a pré-análise da IA de 1 a 5 estrelas na
-- tela de atendimento do Workspace. A avaliação vive na PRÓPRIA linha da
-- triagem (`ia_triagens`) — junto de custo/tokens/duração, fechando o ciclo de
-- auditoria por rodada (Regra de Ouro #9 / Seção 10.2).
--
-- Escrita: continua SEM policy (zero policies de escrita — decisão da 0050).
-- A rota do Workspace verifica o escopo do staff sob RLS (SELECT da triagem
-- com claims) e só então grava pela conexão administrativa, registrando QUEM
-- avaliou (`avaliado_por`) e quando (`avaliado_em`). Reavaliar sobrescreve
-- (o atendente pode mudar de opinião — vale a última).
--
-- Idempotente (reexecução não duplica).

BEGIN;

ALTER TABLE ia_triagens
  ADD COLUMN IF NOT EXISTS avaliacao smallint
    CHECK (avaliacao BETWEEN 1 AND 5),
  ADD COLUMN IF NOT EXISTS avaliado_por uuid REFERENCES perfis(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS avaliado_em timestamptz;

COMMENT ON COLUMN ia_triagens.avaliacao IS
  'Nota do atendente para a pré-análise da IA (1–5 estrelas). Fonte do KPI "notas úteis ≥ 70%" (plano_md_mestre_IA.md, Seção 10.2). NULL = ainda não avaliada.';
COMMENT ON COLUMN ia_triagens.avaliado_por IS
  'Perfil do staff que avaliou a triagem (última avaliação vale).';
COMMENT ON COLUMN ia_triagens.avaliado_em IS
  'Momento da (última) avaliação da triagem pelo staff.';

COMMIT;

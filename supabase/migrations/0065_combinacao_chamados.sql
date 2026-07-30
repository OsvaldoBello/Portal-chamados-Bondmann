-- 0065_combinacao_chamados.sql
-- Decisão de produto 2026-07-30 (pedido do gestor): "combinação de chamados".
--
-- Problema real: quando algo cai para todo mundo ao mesmo tempo (servidor,
-- internet, ERP), 8 pessoas abrem 8 chamados do MESMO incidente. Hoje o setor
-- responde 8 vezes, e o indicador conta 8 demandas — o volume do mês e o TMA
-- ficam inflados por um único evento. O que se quer é: atender UM chamado, com
-- todas as informações dos outros juntas, mantendo todo mundo em cópia para
-- receber as atualizações em conjunto, e os repetidos FORA dos indicadores.
--
-- Modelagem: uma auto-referência em `chamados` (`chamado_principal_id`), não uma
-- tabela de ligação. É 1:N (um principal, N duplicados) e a pergunta que o
-- sistema faz o tempo todo é "esta linha conta?" — uma coluna nula responde isso
-- num índice parcial, sem JOIN em toda agregação do Admin. Nada de status novo
-- no enum: um `COMBINADO` obrigaria a tocar cada `_status_ui`, cada badge, o
-- Kanban de três setores e as triggers de SLA/RESPOSTA_CLIENTE (0061) — o
-- duplicado é encerrado como RESOLVIDO (para o relógio de SLA e sai do quadro) e
-- o que o exclui dos indicadores é a coluna, não o status.
--
-- "Em cópia" reaproveita `chamados_observadores` (0034) na íntegra: o autor do
-- duplicado (e quem já estava em cópia nele) vira observador do principal e
-- ganha, pela RLS que já existe, leitura do chamado + mensagens públicas +
-- Realtime/sino. Nenhuma policy nova é necessária para isso.
--
-- Três garantias de integridade ficam no BANCO (trigger), não só no Python:
--   1. Sem correntes: não se combina com um chamado que já é duplicado de outro
--      (senão a leitura "quem é o principal" vira recursiva) nem se transforma em
--      duplicado um chamado que já é principal de outros.
--   2. Mesmo departamento de destino: combinar entre setores burlaria o escopo
--      de RLS (o staff só enxerga/atende o próprio setor) e moveria observadores
--      para um chamado fora do alcance de quem combinou.
--   3. CLIENTE nunca combina. Isto é o ponto sensível: a trava de coluna
--      `enforce_cliente_so_avaliacao` (0006/0059) é uma LISTA de colunas, então
--      uma coluna nova nasce, por omissão, LIBERADA para o autor num UPDATE do
--      próprio chamado (as policies `chamados_update_cliente_*` já permitem a
--      linha). Sem o reforço abaixo, um funcionário poderia apontar o próprio
--      chamado resolvido para qualquer chamado do setor e se auto-incluir em
--      cópia nele — vazamento de conteúdo de terceiros. A lista é atualizada E o
--      trigger de combinação recusa CLIENTE, em profundidade.

BEGIN;

-- ============================================================
-- 1. Colunas
-- ============================================================
ALTER TABLE chamados
  ADD COLUMN IF NOT EXISTS chamado_principal_id uuid REFERENCES chamados(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS combinado_em         timestamptz,
  ADD COLUMN IF NOT EXISTS combinado_por        uuid REFERENCES perfis(id) ON DELETE SET NULL;

COMMENT ON COLUMN chamados.chamado_principal_id IS
  'Combinação de chamados (0065): NULL = chamado normal; preenchido = duplicado, '
  'encerrado em favor do principal e EXCLUÍDO de todo indicador/fila.';
COMMENT ON COLUMN chamados.combinado_em IS 'Quando a combinação foi feita (escrito pelo trigger).';
COMMENT ON COLUMN chamados.combinado_por IS 'Quem combinou (escrito pelo trigger, a partir de auth.uid()).';

ALTER TABLE chamados DROP CONSTRAINT IF EXISTS chamados_combinacao_nao_auto;
ALTER TABLE chamados ADD CONSTRAINT chamados_combinacao_nao_auto
  CHECK (chamado_principal_id IS NULL OR chamado_principal_id <> id);

-- Índice PARCIAL: a esmagadora maioria das linhas tem a coluna nula, e as
-- consultas são sempre "os duplicados de X" (nunca "todos os nulos").
CREATE INDEX IF NOT EXISTS idx_chamados_principal
  ON chamados(chamado_principal_id) WHERE chamado_principal_id IS NOT NULL;

-- ============================================================
-- 2. Guarda-corpo da combinação (integridade + quem pode)
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_combinacao_chamados()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  v_dep_principal  uuid;
  v_principal_de   uuid;
  v_encontrado     boolean := false;
BEGIN
  -- UPDATE que não mexeu na coluna (o trigger dispara por `UPDATE OF`, mas o
  -- mesmo comando pode estar alterando várias colunas de uma vez). IFs
  -- ANINHADOS de propósito: o Postgres não garante curto-circuito num `AND`, e
  -- referenciar OLD no ramo de INSERT levantaria "record old is not assigned yet".
  IF TG_OP = 'UPDATE' THEN
    IF NEW.chamado_principal_id IS NOT DISTINCT FROM OLD.chamado_principal_id THEN
      RETURN NEW;
    END IF;
  END IF;

  -- `auth.uid() IS NOT NULL` para não travar `admin_connection()`/migrations
  -- (sem claims, `auth_role()` é NULL) — o alvo aqui é o usuário autenticado.
  IF auth.uid() IS NOT NULL AND auth_role() = 'CLIENTE' THEN
    RAISE EXCEPTION 'Somente o staff do setor de destino pode combinar chamados.';
  END IF;

  IF NEW.chamado_principal_id IS NULL THEN
    NEW.combinado_em  := NULL;
    NEW.combinado_por := NULL;
    RETURN NEW;
  END IF;

  SELECT c.departamento_id, c.chamado_principal_id, true
    INTO v_dep_principal, v_principal_de, v_encontrado
    FROM chamados c WHERE c.id = NEW.chamado_principal_id;

  IF NOT v_encontrado THEN
    RAISE EXCEPTION 'Chamado principal % não existe.', NEW.chamado_principal_id;
  END IF;
  IF v_principal_de IS NOT NULL THEN
    RAISE EXCEPTION 'O chamado principal já é um duplicado de outro — combine com o chamado original.';
  END IF;
  IF v_dep_principal IS DISTINCT FROM NEW.departamento_id THEN
    RAISE EXCEPTION 'Só é possível combinar chamados do mesmo departamento de destino.';
  END IF;
  IF EXISTS (SELECT 1 FROM chamados f WHERE f.chamado_principal_id = NEW.id) THEN
    RAISE EXCEPTION 'Este chamado já é o principal de outros — desfaça as combinações antes.';
  END IF;

  NEW.combinado_em  := now();
  NEW.combinado_por := auth.uid();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS enforce_combinacao_chamados ON chamados;
CREATE TRIGGER enforce_combinacao_chamados
  BEFORE INSERT OR UPDATE OF chamado_principal_id ON chamados
  FOR EACH ROW EXECUTE FUNCTION enforce_combinacao_chamados();

-- ============================================================
-- 3. Trava de coluna do CLIENTE — acrescenta as 3 colunas novas
--    (idêntica à 0059 fora isso; ver o cabeçalho desta migration para o porquê)
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF auth_role() = 'CLIENTE' THEN
    IF OLD.status = 'RESOLVIDO' AND NEW.status = 'EM_ATENDIMENTO' THEN
      IF ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
             NEW.titulo, NEW.descricao, NEW.prioridade,
             NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
             NEW.created_at,
             NEW.chamado_principal_id, NEW.combinado_em, NEW.combinado_por)
         IS DISTINCT FROM
         ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
             OLD.titulo, OLD.descricao, OLD.prioridade,
             OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
             OLD.created_at,
             OLD.chamado_principal_id, OLD.combinado_em, OLD.combinado_por)
      THEN
        RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário) ou reabri-lo.';
      END IF;
      IF NEW.resolvido_em IS NOT NULL THEN
        RAISE EXCEPTION 'Reabertura deve limpar resolvido_em.';
      END IF;
      RETURN NEW;
    END IF;

    IF ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
           NEW.titulo, NEW.descricao, NEW.status, NEW.prioridade,
           NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
           NEW.resolvido_em, NEW.created_at,
           NEW.chamado_principal_id, NEW.combinado_em, NEW.combinado_por)
       IS DISTINCT FROM
       ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
           OLD.titulo, OLD.descricao, OLD.status, OLD.prioridade,
           OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
           OLD.resolvido_em, OLD.created_at,
           OLD.chamado_principal_id, OLD.combinado_em, OLD.combinado_por)
    THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

-- ============================================================
-- 4. Indicadores do Marketing (views) — duplicado não conta
--    O resto dos indicadores (Admin/KPIs/fila) é SQL de aplicação e ganha o
--    mesmo filtro em `app/repositories/`; aqui só o que mora no banco.
--
--    ⚠️ Base da reescrita é a **`0041`** (concluídas por mês de RESOLUÇÃO, com
--    as CTEs `abertos`/`concluidos`), NÃO a `0032` que criou as views. Recriar
--    a partir da 0032 compilaria sem erro (mesmas colunas, mesma ordem) e
--    reverteria a 0041 em silêncio — "Concluídas" voltaria a contar pelo mês de
--    abertura. Toda alteração futura destas views deve partir da definição
--    vigente, não da migration que as criou.
-- ============================================================
CREATE OR REPLACE VIEW vw_marketing_volume_mensal AS
WITH abertos AS (
  SELECT
    date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS total,
    count(*) FILTER (WHERE c.status = 'NOVO') AS abertas,
    count(*) FILTER (WHERE c.status NOT IN ('RESOLVIDO', 'NOVO')) AS em_andamento,
    COALESCE(sum(COALESCE(c.volume, 1)), 0)::int AS volume,
    count(*) FILTER (WHERE lower(coalesce(c.origem_demanda, '')) = 'marketing') AS mkt_orig,
    count(*) FILTER (WHERE lower(coalesce(c.origem_demanda, '')) <> 'marketing') AS sol_orig,
    count(*) FILTER (WHERE (COALESCE(c.resolvido_em, now()) - c.created_at) > interval '5 days') AS atrasos,
    round(
      avg(EXTRACT(EPOCH FROM (c.resolvido_em - c.created_at)) / 86400.0)
        FILTER (WHERE c.resolvido_em IS NOT NULL)
    , 1) AS tempo_medio
    FROM chamados c
    JOIN departamentos d ON d.id = c.departamento_id
   WHERE d.nome = 'Marketing'
     AND c.chamado_principal_id IS NULL
   GROUP BY 1
),
concluidos AS (
  SELECT
    date_trunc('month', c.resolvido_em AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS concluidas
    FROM chamados c
    JOIN departamentos d ON d.id = c.departamento_id
   WHERE d.nome = 'Marketing'
     AND c.resolvido_em IS NOT NULL
     AND c.chamado_principal_id IS NULL
   GROUP BY 1
)
SELECT
  COALESCE(a.mes, cc.mes)          AS mes,
  COALESCE(a.total, 0)             AS total,
  COALESCE(cc.concluidas, 0)       AS concluidas,
  COALESCE(a.abertas, 0)           AS abertas,
  COALESCE(a.em_andamento, 0)      AS em_andamento,
  COALESCE(a.volume, 0)            AS volume,
  COALESCE(a.mkt_orig, 0)          AS mkt_orig,
  COALESCE(a.sol_orig, 0)          AS sol_orig,
  COALESCE(a.atrasos, 0)           AS atrasos,
  a.tempo_medio                    AS tempo_medio
  FROM abertos a
  FULL OUTER JOIN concluidos cc ON cc.mes = a.mes;

CREATE OR REPLACE VIEW vw_marketing_setor_mensal AS
SELECT
  date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
  COALESCE(NULLIF(c.setor, ''), 'Outros') AS setor,
  count(*) AS total
  FROM chamados c
  JOIN departamentos d ON d.id = c.departamento_id
 WHERE d.nome = 'Marketing'
   AND c.chamado_principal_id IS NULL
 GROUP BY 1, 2;

ALTER VIEW vw_marketing_volume_mensal SET (security_invoker = true);
ALTER VIEW vw_marketing_setor_mensal  SET (security_invoker = true);

-- ============================================================
-- 5. Hardening (mesmo padrão das 0005/0017/0064): nada disso é RPC.
-- ============================================================
REVOKE EXECUTE ON FUNCTION enforce_combinacao_chamados()  FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION enforce_cliente_so_avaliacao() FROM public, anon, authenticated;

COMMIT;

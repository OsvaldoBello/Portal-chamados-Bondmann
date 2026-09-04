-- 0090_reabertura_autor_sem_papel_cliente.sql
-- Corrige o "Erro interno" (500) ao REABRIR um chamado resolvido quando o autor
-- não tem papel CLIENTE (produção, 2026-08-28, BD-2026-00717 — Anderson Viana,
-- ADMIN da Controladoria, chamado aberto para o TI).
--
-- Cadeia do bug:
--   1. `PortalService.pode_reabrir` (Python) só olha autor + status RESOLVIDO,
--      então o botão aparece — corretamente: reabrir é direito de quem ABRIU.
--   2. `POST /portal/chamados/{id}/reabrir` roda o UPDATE sob RLS. O USING de
--      `chamados_update_cliente_avaliacao` (0006/0014, sem checagem de papel)
--      casa com a linha, então ela ENTRA no UPDATE.
--   3. Nenhum WITH CHECK aceita a linha nova: o da avaliação exige que o status
--      continue RESOLVIDO, e o da reabertura (0059) exige `auth_role() =
--      'CLIENTE'`. Resultado: `42501 new row violates row-level security policy`
--      -> asyncpg levanta -> handler global de `app/main.py` -> 500.
--
-- Repare que a falha NÃO é "0 linhas afetadas" (que seria um no-op silencioso):
-- linha elegível pelo USING + WITH CHECK recusado é ERRO no Postgres. Por isso
-- o usuário vê 500 e não a tela do chamado.
--
-- Alcance: todo autor com papel OPERADOR/ADMIN (líderes de setor, staff de
-- outras filas) que abriu chamado para OUTRO departamento — hoje 31 dos 155
-- perfis de produção. Quem é staff do setor de DESTINO nunca caiu nisto: a
-- `chamados_update_staff` já cobre esse caminho.
--
-- Papel nunca foi o critério certo aqui: reabrir é "isso não foi resolvido de
-- verdade", dito por quem PEDIU — a mesma leitura que já vale em
-- `chamados_update_cliente_avaliacao`, que desde a 0006 não olha papel nenhum.
-- O nome da policy segue com "cliente" por consistência com a irmã (ali
-- "cliente" quer dizer AUTOR/solicitante, não o papel `CLIENTE`).

-- ============================================================
-- 1. RLS — a reabertura passa a ser do AUTOR, qualquer que seja o papel.
--    A transição continua restrita a RESOLVIDO -> EM_ATENDIMENTO (USING e
--    WITH CHECK), que é o que impede usar esta policy como porta lateral para
--    qualquer outro status.
-- ============================================================
DROP POLICY IF EXISTS chamados_update_cliente_reabertura ON chamados;

CREATE POLICY chamados_update_cliente_reabertura ON chamados
  FOR UPDATE TO authenticated
  USING (cliente_id = (SELECT auth.uid()) AND status = 'RESOLVIDO')
  WITH CHECK (cliente_id = (SELECT auth.uid()) AND status = 'EM_ATENDIMENTO');

-- ============================================================
-- 2. Trava de coluna: `enforce_cliente_so_avaliacao` (0006/0059/0072) só valia
--    para `auth_role() = 'CLIENTE'`. Com a policy acima liberando o autor de
--    qualquer papel, esse recorte deixaria a defesa em profundidade sem efeito
--    justamente para quem passou a ser alcançado — um líder de outro setor
--    poderia, no mesmo UPDATE da reabertura, reescrever título, prioridade ou
--    operador do próprio chamado. (Pela irmã da avaliação isso já era possível
--    desde a 0006; esta migration fecha os dois casos de uma vez.)
--
--    Novo critério: está agindo como AUTOR quem é papel CLIENTE (recorte
--    original) OU quem é o `cliente_id` da linha sem ser staff do setor de
--    DESTINO dela — exatamente o complemento do USING de
--    `chamados_update_staff`. Staff atendendo o próprio setor (incluindo os
--    autoatendimentos de Marketing/RH/TI, 0038/0042/0047) continua fora da
--    trava, como sempre esteve.
--
--    `admin_connection`/jobs (sem claims) seguem transparentes: `auth_role()`
--    e `auth.uid()` são NULL, nenhum dos dois ramos casa, e o COALESCE final
--    evita que o NULL caia dentro da trava por acidente.
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_como_autor             boolean;
  v_outras_colunas_mudaram boolean;
BEGIN
  v_como_autor := COALESCE(
    auth_role() = 'CLIENTE'
    OR (
      OLD.cliente_id = auth.uid()
      AND NOT (auth_departamento_id() IS NOT NULL
               AND OLD.departamento_id = auth_departamento_id())
    ),
    false);

  IF NOT v_como_autor THEN
    RETURN NEW;
  END IF;

  v_outras_colunas_mudaram :=
    ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
        NEW.titulo, NEW.descricao, NEW.prioridade,
        NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
        NEW.created_at,
        NEW.chamado_principal_id, NEW.combinado_em, NEW.combinado_por,
        NEW.prazo_projeto_dias, NEW.projeto_em)
    IS DISTINCT FROM
    ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
        OLD.titulo, OLD.descricao, OLD.prioridade,
        OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
        OLD.created_at,
        OLD.chamado_principal_id, OLD.combinado_em, OLD.combinado_por,
        OLD.prazo_projeto_dias, OLD.projeto_em);

  -- Reabertura pelo autor (0059): RESOLVIDO -> EM_ATENDIMENTO, zerando resolvido_em.
  IF OLD.status = 'RESOLVIDO' AND NEW.status = 'EM_ATENDIMENTO' THEN
    IF v_outras_colunas_mudaram THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário) ou reabri-lo.';
    END IF;
    IF NEW.resolvido_em IS NOT NULL THEN
      RAISE EXCEPTION 'Reabertura deve limpar resolvido_em.';
    END IF;
    RETURN NEW;
  END IF;

  -- Transição do SISTEMA (0061): mensagem do autor no chat de TI/RH.
  IF NEW.status = 'RESPOSTA_CLIENTE' AND OLD.status IN ('EM_ATENDIMENTO', 'AGUARDANDO') THEN
    IF v_outras_colunas_mudaram OR NEW.resolvido_em IS DISTINCT FROM OLD.resolvido_em THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
    END IF;
    RETURN NEW;
  END IF;

  -- Demais UPDATEs do autor: só nota/comentário/avaliacao_em podem mudar.
  IF v_outras_colunas_mudaram
     OR NEW.status IS DISTINCT FROM OLD.status
     OR NEW.resolvido_em IS DISTINCT FROM OLD.resolvido_em
  THEN
    RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
  END IF;
  RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION enforce_cliente_so_avaliacao() FROM public, anon, authenticated;

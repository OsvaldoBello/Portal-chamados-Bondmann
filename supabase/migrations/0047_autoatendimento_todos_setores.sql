-- 0047_autoatendimento_todos_setores.sql
-- Decisão de produto 2026-07-20: a exceção de autoatendimento (0038/0042, hoje só
-- Marketing/RH) generaliza para TODOS os departamentos, TI incluído. Motivação: hoje
-- um chamado aberto por alguém do próprio setor de destino (ex.: TI abrindo pra TI)
-- fica de fora do Kanban/fila (`FilaRepo.fila` esconde "não é de fora do setor") e o
-- autor não pode se autoatender — na prática, o setor não conseguia usar o próprio
-- chamado como ferramenta de organização interna. Caso concreto: a diretoria quer
-- abrir um chamado (p.ex. para o TI) sem entrar na plataforma como staff; alguém do
-- próprio TI abre em nome da demanda e precisa que ela apareça no quadro do setor,
-- igual já acontece no Marketing/RH.
--
-- Sem migration de policy nova: `chamados_update_staff`/`mensagens_insert` (0042) já
-- leem `departamentos.autoatendimento` de forma genérica (nenhum nome de setor
-- hardcoded) — só a COLUNA de dados precisa mudar, não a policy.

BEGIN;

-- 1) Generaliza a exceção pros setores já cadastrados.
UPDATE departamentos SET autoatendimento = true WHERE autoatendimento = false;

-- 2) Novo default true — setor cadastrado depois desta migration (`AdminRepo.criar_departamento`
--    não passa o campo, sempre usou o default da coluna) já nasce com a mesma regra,
--    sem depender de UPDATE manual futuro.
ALTER TABLE departamentos ALTER COLUMN autoatendimento SET DEFAULT true;

COMMIT;

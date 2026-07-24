-- 0057_ti_projetos_status.sql
-- Novo status de Kanban exclusivo do TI: "Projetos" (PROJETOS) — coluna para
-- demandas de projeto (trabalho de maior fôlego, sem vínculo de atendimento
-- reativo a um solicitante), ao lado do fluxo clássico NOVO -> EM_ATENDIMENTO ->
-- AGUARDANDO -> RESOLVIDO. Mesmo padrão da 0024/0043 (Marketing: A_FAZER /
-- AGUARDANDO_TERCEIROS) — um valor de enum novo não pode ser usado na mesma
-- transação em que foi criado, por isso fica isolado nesta migration própria;
-- a fiação da UI (app/routes/workspace.py::_status_ui) vem em código, sem
-- precisar de outra migration.
ALTER TYPE status_chamado ADD VALUE IF NOT EXISTS 'PROJETOS';

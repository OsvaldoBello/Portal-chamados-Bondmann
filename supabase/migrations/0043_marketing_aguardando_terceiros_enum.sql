-- 0043_marketing_aguardando_terceiros_enum.sql
-- Novo status de Kanban do Marketing: "Aguardando terceiros" (chamado
-- travado esperando um fornecedor/parceiro externo, não o cliente interno).
-- Fica entre EM_ATENDIMENTO e AGUARDANDO (que continua sendo "Aguardando
-- Validação" — esperando o solicitante). Isolado em migration própria: um
-- valor de enum novo não pode ser usado na mesma transação em que foi
-- criado (mesmo padrão da 0024, que introduziu A_FAZER).
ALTER TYPE status_chamado ADD VALUE IF NOT EXISTS 'AGUARDANDO_TERCEIROS';

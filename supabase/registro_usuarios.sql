-- registro_usuarios.sql  (REFERÊNCIA — NÃO é migration; não roda no deploy)
-- ---------------------------------------------------------------------------
-- Cadastro de colaboradores é feito DIRETO no Supabase (não há signup público).
-- Fluxo:
--   1) Criar o usuário em Authentication > Users (Add user) no painel do Supabase,
--      ou via Admin API. O trigger `handle_new_user` cria automaticamente um
--      `perfis` com papel CLIENTE, vinculado à org interna (Bondmann).
--   2) Se o colaborador for STAFF (responde chamados), promover o papel e definir
--      o DEPARTAMENTO com os comandos abaixo (rodar no SQL Editor como serviço).
--
-- Papéis: CLIENTE (funcionário que abre chamado) · OPERADOR/ADMIN (respondem).
-- Departamentos: TI, RH, Marketing (tabela `departamentos`, gerenciável).
-- Regra de acesso (migration 0010):
--   * TI            -> ACESSO TOTAL (vê/atende todos os chamados; gere catálogos).
--   * RH / Marketing -> veem os chamados do SEU setor + os que ELES abriram;
--                       podem abrir chamados para QUALQUER departamento.
--   * Funcionário    -> cria chamados e vê APENAS os que criou (CLIENTE, sem depto).
-- "Staff" = role OPERADOR/ADMIN + um departamento. Acesso total = staff em 'TI'.
-- ---------------------------------------------------------------------------

-- Colaborador que responde chamados de RH (staff do setor de RH):
UPDATE perfis
   SET role = 'OPERADOR',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'colaborador.rh@bondmann.com.br');

-- Colaborador que responde chamados de Marketing:
UPDATE perfis
   SET role = 'OPERADOR',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'Marketing')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'colaborador.mkt@bondmann.com.br');

-- ACESSO TOTAL ao sistema = colaborador do departamento TI:
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'ti@bondmann.com.br');

-- Reverter para funcionário comum (CLIENTE, sem departamento):
-- UPDATE perfis SET role = 'CLIENTE', departamento_id = NULL WHERE id = '...';

-- Adicionar um novo departamento (super-admin também pode fazer pela UI futura):
-- INSERT INTO departamentos (nome) VALUES ('Financeiro');

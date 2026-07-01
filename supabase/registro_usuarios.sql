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
-- Regra: staff com `departamento_id` definido só vê os chamados do seu setor;
--        um ADMIN com `departamento_id` NULO é SUPER-ADMIN (vê todos).
-- ---------------------------------------------------------------------------

-- Tornar um colaborador OPERADOR de um departamento (ex.: RH):
UPDATE perfis
   SET role = 'OPERADOR',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'colaborador@bondmann.com.br');

-- Tornar ADMIN de um departamento (ex.: Marketing) — vê só o Marketing:
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'Marketing')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'gestor.mkt@bondmann.com.br');

-- Tornar SUPER-ADMIN (vê TODOS os departamentos) — departamento_id NULO:
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = NULL
 WHERE id = (SELECT id FROM auth.users WHERE email = 'ti.master@bondmann.com.br');

-- Reverter para funcionário comum (CLIENTE, sem departamento):
-- UPDATE perfis SET role = 'CLIENTE', departamento_id = NULL WHERE id = '...';

-- Adicionar um novo departamento (super-admin também pode fazer pela UI futura):
-- INSERT INTO departamentos (nome) VALUES ('Financeiro');

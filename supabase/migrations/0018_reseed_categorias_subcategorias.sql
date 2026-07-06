-- 0018_reseed_categorias_subcategorias.sql
-- Reseed do catálogo a partir do relatório real de chamados
-- (Relatorio Cliente Analitico.xlsx, coluna "Categoria"): 15 categorias / 37 subcategorias.
-- Substitui o seed anterior (0015_subcategorias). Parsing: "Categoria / Subcategoria" split no 1º "/".
-- A categoria "Criação" mantém, por decisão, subcategorias com "/" internas.

BEGIN;

-- 1. Desvincula chamados do catálogo antigo (FK ON DELETE RESTRICT).
--    A categorização histórica dos chamados é zerada; os chamados são preservados.
UPDATE chamados SET categoria_id = NULL, subcategoria_id = NULL;

-- 2. Limpa o catálogo atual.
DELETE FROM subcategorias;
DELETE FROM categorias;

-- 3. Insere as novas categorias.
INSERT INTO categorias (nome) VALUES
  ('Atendimento de usuário'),
  ('Sistemas'),
  ('Acessos'),
  ('Hardware'),
  ('Sistemas Bondmann'),
  ('Suporte a Impressoras'),
  ('Suporte ao E-mail'),
  ('Criação'),
  ('Software'),
  ('Antivírus'),
  ('Visita Cliente'),
  ('Telefonia'),
  ('Rede'),
  ('Servidor'),
  ('Backup & Restore');

-- 4. Insere as subcategorias, vinculadas por nome da categoria-mãe.
INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, v.sub
FROM categorias c
JOIN (VALUES
  ('Atendimento de usuário', 'Configuração Geral'),
  ('Atendimento de usuário', 'Orientação Técnica'),
  ('Sistemas', 'Configuração'),
  ('Sistemas', 'Acionamento Terceiros'),
  ('Acessos', 'Exclusão de Acesso'),
  ('Acessos', 'Acesso Pastas'),
  ('Acessos', 'Acesso Servidores'),
  ('Hardware', 'Movimentação Equipamento'),
  ('Hardware', 'Troca de peça'),
  ('Hardware', 'Limpeza PC'),
  ('Sistemas Bondmann', 'Erros de sistema'),
  ('Sistemas Bondmann', 'Layout de relatório'),
  ('Sistemas Bondmann', 'Ajuste de regra'),
  ('Sistemas Bondmann', 'Desenvolvimento de Relatório'),
  ('Sistemas Bondmann', 'Consultoria'),
  ('Sistemas Bondmann', 'Criação de regra'),
  ('Sistemas Bondmann', 'Invent Bank'),
  ('Sistemas Bondmann', 'Invent NFe TaxOne'),
  ('Suporte a Impressoras', 'Configuração'),
  ('Suporte a Impressoras', 'Instalação'),
  ('Suporte ao E-mail', 'Configuração'),
  ('Criação', 'Alteração de Usuário / Bloquear'),
  ('Criação', 'Alteração de Usuário / Novo Usuário'),
  ('Criação', 'Alteração de Usuário / Alterar Senha'),
  ('Criação', 'Alteração de Usuário / Alterar Dados Usuário'),
  ('Criação', 'Alteração de Usuário / Editar Permissões'),
  ('Software', 'Configurar'),
  ('Software', 'Instalar'),
  ('Antivírus', 'Revisão'),
  ('Antivírus', 'Instalação'),
  ('Visita Cliente', 'Visita Técnica'),
  ('Telefonia', 'Configurações Gerais'),
  ('Rede', 'Link Internet'),
  ('Rede', 'Rede Interna'),
  ('Rede', 'Mapeamento Pastas'),
  ('Servidor', 'Configuração'),
  ('Backup & Restore', 'Verificação')
) AS v(cat, sub) ON v.cat = c.nome;

COMMIT;

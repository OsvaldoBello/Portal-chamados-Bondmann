-- 0026_categorias_ti_docx.sql
-- Reseed do catálogo do TI a partir do documento de referência
-- "Categorias_Portal_Suporte.docx" (07/07/2026): 12 categorias / 62 subcategorias,
-- levantadas da análise de 445 chamados reais. Substitui o seed do TI feito em
-- 0018 (15 categorias / 37 subcategorias). Marketing (0019) e RH (0021) não são
-- afetados — o filtro por departamento_id = 'TI' escopa a limpeza e o reseed.
--
-- Nota: o próprio documento resume "52 subcategorias" no texto de abertura, mas
-- a lista real (mesmo estilo de marcador em todos os itens) enumera 62 — a
-- migration segue a lista, não o resumo.

BEGIN;

-- 1. Desvincula chamados do catálogo antigo do TI (FK categoria_id/subcategoria_id
--    ON DELETE RESTRICT). Defensivo: hoje nenhum chamado referencia essas linhas,
--    mas a migration deve ser segura mesmo se isso mudar antes de rodar.
UPDATE chamados
   SET categoria_id = NULL, subcategoria_id = NULL
 WHERE categoria_id IN (
   SELECT id FROM categorias WHERE departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI')
 );

-- 2. Limpa o catálogo atual do TI (subcategorias antes, por FK).
DELETE FROM subcategorias
 WHERE categoria_id IN (
   SELECT id FROM categorias WHERE departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI')
 );
DELETE FROM categorias
 WHERE departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI');

-- 3. Insere as 12 categorias do TI.
INSERT INTO categorias (nome, departamento_id)
SELECT v.nome, (SELECT id FROM departamentos WHERE nome = 'TI')
FROM (VALUES
  ('Sistemas ERP (SAP / Bondmann)'),
  ('Usuários e Acessos'),
  ('Aplicativo Móvel (WMW / Wtools)'),
  ('E-mail e Comunicação'),
  ('Software e Produtividade'),
  ('Impressão e Digitalização'),
  ('Hardware e Infraestrutura'),
  ('Segurança e Continuidade'),
  ('Dados e Operações'),
  ('Plataformas Externas e Fiscais'),
  ('UBD'),
  ('Desenvolvimento')
) AS v(nome);

-- 4. Insere as 62 subcategorias, vinculadas pela categoria-mãe (escopadas ao TI).
INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, v.sub
FROM categorias c
JOIN (VALUES
  ('Sistemas ERP (SAP / Bondmann)', 'Erro / Falha no Sistema'),
  ('Sistemas ERP (SAP / Bondmann)', 'Configuração do Sistema'),
  ('Sistemas ERP (SAP / Bondmann)', 'Permissões e Acessos no SAP'),
  ('Sistemas ERP (SAP / Bondmann)', 'Add-ons e Plugins SAP'),
  ('Sistemas ERP (SAP / Bondmann)', 'Integração WMW ↔ SAP'),
  ('Sistemas ERP (SAP / Bondmann)', 'Layout de Relatório'),
  ('Sistemas ERP (SAP / Bondmann)', 'Criação / Ajuste de Regra de Negócio'),
  ('Sistemas ERP (SAP / Bondmann)', 'Consultoria / Orientação SAP'),

  ('Usuários e Acessos', 'Criação de Novo Usuário'),
  ('Usuários e Acessos', 'Desligamento / Bloqueio de Acesso'),
  ('Usuários e Acessos', 'Alteração de Perfil / Cargo'),
  ('Usuários e Acessos', 'Redefinição de Senha'),
  ('Usuários e Acessos', 'Permissões de Pasta / Compartilhamento'),
  ('Usuários e Acessos', 'Acesso a Servidores / VPN'),

  ('Aplicativo Móvel (WMW / Wtools)', 'Instalação e Configuração do App'),
  ('Aplicativo Móvel (WMW / Wtools)', 'Erros e Falhas no App'),
  ('Aplicativo Móvel (WMW / Wtools)', 'Sincronização de Pedidos'),

  ('E-mail e Comunicação', 'Configuração de Conta de E-mail'),
  ('E-mail e Comunicação', 'Grupos de Distribuição / Listas de E-mail'),
  ('E-mail e Comunicação', 'Gestão de Caixa de Correio (Quota / Arquivo)'),
  ('E-mail e Comunicação', 'Encaminhamento / Redirecionamento de E-mail'),
  ('E-mail e Comunicação', 'Telefonia / Ramal'),
  ('E-mail e Comunicação', 'Gravação de Ligações'),

  ('Software e Produtividade', 'Instalação de Software'),
  ('Software e Produtividade', 'Configuração de Software'),
  ('Software e Produtividade', 'Microsoft 365 / Teams / Office'),
  ('Software e Produtividade', 'OneDrive / SharePoint / Sincronização em Nuvem'),
  ('Software e Produtividade', 'Certificado Digital'),

  ('Impressão e Digitalização', 'Instalação de Impressora'),
  ('Impressão e Digitalização', 'Configuração / Reconfiguração de Impressora'),
  ('Impressão e Digitalização', 'Scanner / Digitalização para E-mail'),
  ('Impressão e Digitalização', 'Software de Etiquetas (BarTender)'),
  ('Impressão e Digitalização', 'Troca de Toner / Suprimentos'),

  ('Hardware e Infraestrutura', 'Manutenção / Troca de Peça'),
  ('Hardware e Infraestrutura', 'Formatação / Reinstalação de SO'),
  ('Hardware e Infraestrutura', 'Aquisição / Movimentação de Equipamento'),
  ('Hardware e Infraestrutura', 'Limpeza e Manutenção Preventiva'),
  ('Hardware e Infraestrutura', 'Armazenamento / Espaço em Disco'),
  ('Hardware e Infraestrutura', 'Rede Interna / Cabeamento'),
  ('Hardware e Infraestrutura', 'Acesso à Internet'),
  ('Hardware e Infraestrutura', 'Mapeamento de Pastas de Rede'),
  ('Hardware e Infraestrutura', 'Servidor'),
  ('Hardware e Infraestrutura', 'Relógio Ponto'),

  ('Segurança e Continuidade', 'Antivírus / Proteção de Endpoint'),
  ('Segurança e Continuidade', 'Backup e Restauração'),
  ('Segurança e Continuidade', 'Câmeras de Segurança / CFTV'),

  ('Dados e Operações', 'Extração de Dados / Listagem de Clientes'),
  ('Dados e Operações', 'Tabela de Preços'),
  ('Dados e Operações', 'Relatórios Automáticos (Bolsa / AF / AR)'),
  ('Dados e Operações', 'Correção de NF / Pedido'),
  ('Dados e Operações', 'Pedidos Duplicados'),
  ('Dados e Operações', 'Envio de XML / NF-e'),

  ('Plataformas Externas e Fiscais', 'Portais Governamentais (SEFAZ, GNRE, Sintegra)'),
  ('Plataformas Externas e Fiscais', 'BankPlus e Integrações Financeiras'),
  ('Plataformas Externas e Fiscais', 'Conformidade Regulatória'),

  ('UBD', 'Alteração de vínculo de liderança'),
  ('UBD', 'Questões em formulários'),
  ('UBD', 'Desativação de curso'),
  ('UBD', 'Configurações gerais'),

  ('Desenvolvimento', 'Automações e Scripts'),
  ('Desenvolvimento', 'Dashboards e Painéis'),
  ('Desenvolvimento', 'Desenvolvimento de relatório')
) AS v(cat, sub)
  ON v.cat = c.nome
 AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI');

COMMIT;

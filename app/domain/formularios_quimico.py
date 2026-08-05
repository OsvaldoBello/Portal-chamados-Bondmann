"""Schema dos formulários dinâmicos do departamento Químico (feature 2026-07).

Cada categoria do Químico tem um **layout de campos diferente**, migrado dos
Microsoft Forms do setor (anexados pelo usuário em 2026-07-21):
  - FB033-Registro de Ocorrências
  - Solicitação de visita técnica
  - Solicitação de análise em amostra externa

Categoria "Solicitação de Desenvolvimento" (migration 0077, 2026-08-05):
migrada do formulário Word do setor (anexado pelo usuário), voltada a pedidos
de novo produto avaliados pela gestão de P&D — não tem um fluxo de aprovação
modelado no sistema (isso continua manual, fora do portal); o formulário só
coleta a justificativa de negócio e exibe um aviso estático de que a análise é
feita pela gestão de P&D.

Em vez de uma coluna por campo (como o precedente ad-hoc do Marketing —
migration 0024), os campos são definidos aqui, em código, e as respostas são
gravadas em `chamados.dados_formulario` (jsonb, migration 0049) como um objeto
``{name: valor}``. Valores são ``str`` para a maioria dos tipos e ``list[str]``
para ``checkbox_multi`` (perguntas de múltipla escolha).

Todos os *dropdowns* do form original (Região, Supervisor, Gerente, Produto —
no Registro de Ocorrência; Região do cliente — na Solicitação de Visita
Técnica) já foram convertidos para `select` com as listas reais, anexadas
pelo usuário em 2026-07-22. "Região do cliente" usa a mesma lista de
`_REGIOES` (confirmado pelo usuário: é o mesmo dropdown).

Ajustes solicitados pelo usuário no chamado BOND-2026-00569 (2026-07-23):
Registro de Ocorrência perdeu Representante, Tipo de Ocorrência e as 7
perguntas de Análise de Causa Provável; Solicitação de Visita Técnica perdeu
Solicitante e Unidade Bondmann de Atendimento e ganhou Estado (após Cidade);
Solicitação de Análise Laboratorial perdeu Identificação do solicitante.

O `name` de cada campo é a chave estável em `dados_formulario` — trocar um
`name` depois de ter chamados gravados exige cuidado/migração de dados.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

# Nomes das categorias do Químico. DEVEM casar exatamente com os `nome` semeados
# na migration 0049 — é por eles que a rota casa a categoria escolhida com o
# layout. Centralizados aqui para não repetir a string solta pelo código.
CAT_OCORRENCIA = "Registro de Ocorrência"
CAT_VISITA = "Solicitação de Visita Técnica"
CAT_ANALISE = "Solicitação de Análise Laboratorial"
CAT_DESENVOLVIMENTO = "Solicitação de Desenvolvimento"

# Tipos de campo suportados pelo partial `_campos_quimico.html` e pela validação.
# ``checkbox_multi``: 0..N opções marcadas — valor gravado é ``list[str]``.
TIPOS_VALIDOS = {"text", "textarea", "select", "date", "number", "email", "tel", "checkbox_multi"}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Unidades Bondmann (radio nos forms de Visita Técnica e Análise Laboratorial).
_UNIDADES = ("Matriz Canoas/RS", "Filial Indaiatuba/SP")

# Regiões comerciais (dropdown "Região" do FB033-Registro de Ocorrências) —
# lista anexada pelo usuário em 2026-07-22 (prints do Microsoft Forms). Os
# códigos têm lacunas na numeração no material original (regiões descontinuadas
# / não usadas atualmente) — mantidas como estão, na ordem exibida no form.
_REGIOES = (
    "001-COLOMBO", "002-SAO MIGUEL DO OESTE", "003-CURITIBA", "004-CATANDUVA",
    "005-CHAPECO", "006-IGREJINHA", "007-GRAVATAI", "008-SAO LEOPOLDO",
    "009-BENTO GONCALVES", "010-JARAGUA DO SUL", "011-PANAMBI",
    "013-CACHOEIRA DO SUL", "015-CASCAVEL", "016-CRICIUMA", "018-PATO BRANCO",
    "019-LAGES", "020-JOINVILLE", "021-SOROCABA", "022-CAXIAS DO SUL",
    "023-PALHOCA", "024-LAJEADO", "025-LITORAL", "026-BAGE", "027-ERECHIM",
    "028-SP-NORTE/OESTE", "029-BARUERI", "031-BAURU", "033-POA (ZONA NORTE)",
    "034-BLUMENAU", "035-SANTA CRUZ DO SUL", "037-PRESIDENTE PRUDENTE",
    "038-SANTA MARIA", "039-PONTA GROSSA", "040-SANTA ROSA", "041-PELOTAS",
    "042-MONTENEGRO", "043-SAO JOSE DO RIO PRETO", "044-VIDEIRA",
    "045-MARINGA", "046-BRUSQUE", "047-LONDRINA", "048-INDAIATUBA",
    "049-ARAUCARIA", "051-LIMEIRA", "053-RIBEIRAO PRETO", "054-CANOAS",
    "056-MOGI MIRIM", "057-VACARIA", "059-TOLEDO", "060-SAO JOAO DA BOA VISTA",
    "062-CIANORTE", "063-APUCARANA", "064-RIO CLARO", "065-CONTAGEM",
    "066-BELO HORIZONTE", "069-DIVINOPOLIS", "071-MARILIA",
    "072-NOVO HAMBURGO", "073-ARACATUBA", "075-POCOS DE CALDAS",
    "077-PASSO FUNDO", "079-SAO JOSE DOS PINHAIS", "080-APARECIDA DO NORTE",
    "081-JABOTICABAL", "082-ARARAQUARA", "083-SIQUEIRA CAMPOS",
    "084-UBERLANDIA", "085-FRANCA", "086-NITEROI", "087-SP-LESTE",
    "088-CAMPOS DOS GOYTACAZES", "089-PIRACICABA", "090-DUQUE DE CAXIAS",
    "091-GUARAPUAVA", "093-SAO JOSE DOS CAMPOS", "094-PARANAGUA",
    "097-BRAGANCA PAULISTA", "098-JUNDIAI", "099-UBERABA", "100-FOZ DO IGUACU",
    "101-UMUARAMA", "102-FLORIANOPOLIS", "103-BOTUCATU", "104-ASSIS",
    "105-OURINHOS", "106-FRANCISCO BELTRAO", "125-BAIXADA SANTISTA",
    "126-PORTO FERREIRA", "127-BETIM", "128-SUMARE", "129-RIO DE JANEIRO",
    "130-SETE LAGOAS", "131-SP-SUL", "132-SP-ABCD", "134-JUIZ DE FORA",
    "136-VALE DO ACO", "137-VOLTA REDONDA", "138-NOVA FRIBURGO",
    "144-CATALAO", "145-ANAPOLIS", "146-CRISTALINA", "147-BRASILIA",
    "150-FREDERICO WESTPHALEN", "151-ALEGRETE", "152-PORTO UNIAO",
    "163-GOIANIA", "164-ITUMBIARA", "166-CONSELHEIRO LAFAIETE",
    "167-BARBACENA", "168-CURVELO", "169-VARGINHA", "170-PATOS DE MINAS",
    "173-RIO DO SUL", "VENDA DIRETA",
)

# Supervisores (dropdown "Supervisor" do FB033-Registro de Ocorrências) —
# lista anexada pelo usuário em 2026-07-22 (prints do Microsoft Forms).
_SUPERVISORES = (
    "CHRISTIAN ALVES SEVERO", "CLAUDIO DA SILVA GONCALVES",
    "EDUARDO HENRIQUE MAGALHAES TONON PASQUOT", "ELIEZER JERKE",
    "EQUIPE DIRETA SP", "EQUIPE MG 2", "EQUIPE MG 3", "EQUIPE PR 5",
    "EQUIPE PR3", "EQUIPE RS 5", "EQUIPE SC 2", "EQUIPE SP 5", "EQUIPE SP 6",
    "HELMANO SACRAMENTO SILVA", "IGOR RIBEIRO GONZALEZ",
    "JOAO ANTONIO PONTES DE CASTRO", "JORGE AUGUSTO GIORGIANI",
    "JORGE AURELIO CUNHA VAN LARE", "JULIANO FREITAS NICHES",
    "MARCELO ALMEIDA NEVES", "MAURICIO ROSA DO NASCIMENTO",
    "ROGERIO DA COSTA CARDOSO", "RONALDO RAGANHAN", "VENDA DIRETA",
)

# Gerentes (dropdown "Gerente" do FB033-Registro de Ocorrências) — lista
# anexada pelo usuário em 2026-07-22. "VENDA DIRETA" é a mesma designação de
# canal direto usada em Região/Supervisor, reaparece aqui como opção própria.
_GERENTES = (
    "ANDRE LUIZ MANDELLI", "BRUNO TIARA DA SILVA",
    "WALLYSSON ALEXSSANDRO DE ANDRADE MEDEIROS", "VENDA DIRETA",
)

# Produtos (dropdown "Produto" do FB033-Registro de Ocorrências) — lista
# anexada pelo usuário em 2026-07-22, na ordem exibida no form (não é
# estritamente alfabética no material original — ex.: as variantes de
# "DESINFETANTES BONDMANN" aparecem fora de ordem).
_PRODUTOS = (
    "26", "ADITIVO 1090", "ADITIVO 967", "ALKARES", "ALLIMP",
    "ANTIESPUMANTE 1013", "AW-B 32", "AW-B 46", "AW-B 68", "B-DRILL",
    "BONADEA", "BONDGLASS", "BRIL", "BRITE", "CAR", "CITROMEC", "CL 1000",
    "CLINOX", "CONCENTRADO", "CONCRET", "CONTROL 100", "COOL", "DEGRAX 25",
    "DESINFETANTES BONDMANN (LAVANDA)", "DESINFETANTES BONDMANN (FEMME)",
    "DESINFETANTES BONDMANN (FLORAL)", "DESMOLDAX", "DETSOLV", "DFD", "DRY10",
    "ECOSOLV", "FLOT-Q", "FORTE", "FREE 970", "GRAXCAR II", "LB 10", "LB 20",
    "LIMPTEC 100", "LUBRY", "LW-B 32", "LW-B 46", "LW-B 68", "NEUTRORUST",
    "O.S.T", "OIL 30", "OX", "PASSIVOX", "POT", "PROTEC", "PROTETIVO",
    "PROTEVEG", "SABOLIQ", "SAW", "SHAMP", "SHIP", "SNAP", "TORRE",
    "ULTRA (LIPTO)", "VIDRO", "WAY 45 - B", "WAY 45 - CF", "WAY 45 - E",
    "WAY 45 - V", "WAY 45 - X", "WAY 45 - Y", "ZINTEX",
)

# Assunto/Descrição automáticos (2026-07-22): a tela de abertura esconde esses
# dois campos genéricos quando o departamento é o Químico — o layout dinâmico
# da categoria já cobre a informação. Por categoria, o campo "identificador"
# vira o Assunto (com o nome da categoria) e o campo "narrativa" vira a
# Descrição — ambos já fazem parte do schema de cada categoria.
_CAMPO_IDENTIFICADOR: dict[str, str] = {
    CAT_OCORRENCIA: "nome_empresa_cliente",
    CAT_VISITA: "cliente_visitado",
    CAT_ANALISE: "identificacao_cliente",
    CAT_DESENVOLVIMENTO: "objetivo_desenvolvimento",
}
_CAMPO_NARRATIVA: dict[str, str] = {
    CAT_OCORRENCIA: "descricao_situacao",
    CAT_VISITA: "objetivo_visita",
    CAT_ANALISE: "descricao_amostra",
    CAT_DESENVOLVIMENTO: "justificativa",
}

# Aviso estático exibido ao final do formulário de certas categorias (não é um
# campo — não entra em `dados_formulario`). Categoria sem entrada aqui não
# mostra nada.
OBSERVACAO_POR_CATEGORIA: dict[str, str] = {
    CAT_DESENVOLVIMENTO: (
        "A solicitação será avaliada pela gestão de P&D e, caso aprovada, "
        "será iniciado o projeto de desenvolvimento."
    ),
}


def observacao_categoria(nome_categoria: str | None) -> str:
    """Aviso estático da categoria (rodapé do formulário), ou string vazia."""
    if not nome_categoria:
        return ""
    return OBSERVACAO_POR_CATEGORIA.get(nome_categoria, "")


def titulo_e_descricao_automaticos(
    nome_categoria: str | None, dados: dict[str, Any]
) -> tuple[str, str]:
    """Deriva (Assunto, Descrição) de um chamado do Químico a partir das
    respostas do formulário dinâmico, para as categorias cuja tela de abertura
    não pede esses dois campos separadamente. ``dados`` é o retorno "limpo" de
    ``validar_payload`` (chave → valor já validado). Sempre retorna algo não
    vazio (cai para o nome da categoria se o campo-fonte não veio preenchido)
    — categoria sem layout (fora do Químico) retorna ``("", "")``."""
    if not nome_categoria or nome_categoria not in _CAMPO_IDENTIFICADOR:
        return "", ""
    identificador = str(dados.get(_CAMPO_IDENTIFICADOR[nome_categoria], "") or "")
    titulo = f"{nome_categoria} — {identificador}" if identificador else nome_categoria
    descricao = str(dados.get(_CAMPO_NARRATIVA[nome_categoria], "") or "") or nome_categoria
    return titulo[:160], descricao


@dataclass(frozen=True)
class CampoDef:
    """Definição de um campo dinâmico de formulário.

    ``name`` é a chave em ``dados_formulario`` (e o sufixo do input HTML
    ``campo__<name>``). ``opcoes`` se aplica a ``select``/``checkbox_multi``.
    ``min_chars`` valida um tamanho mínimo em campos de texto (ex.: Lote, Fone).
    """

    name: str
    label: str
    tipo: str = "text"
    obrigatorio: bool = False
    opcoes: tuple[str, ...] = field(default_factory=tuple)
    ajuda: str = ""
    min_chars: int = 0


# Ordem da lista = ordem de exibição no formulário.
CAMPOS_POR_CATEGORIA: dict[str, tuple[CampoDef, ...]] = {
    # FB033-Registro de Ocorrências
    CAT_OCORRENCIA: (
        # Identificação da Região
        CampoDef("regiao", "Região", "select", obrigatorio=True, opcoes=_REGIOES),
        CampoDef("supervisor", "Supervisor", "select", obrigatorio=True, opcoes=_SUPERVISORES),
        CampoDef("gerente", "Gerente", "select", obrigatorio=True, opcoes=_GERENTES),
        # Identificação do Local
        CampoDef("nome_empresa_cliente", "Nome da Empresa (Cliente)", "text", obrigatorio=True),
        CampoDef("codigo_cliente", "Código do Cliente", "text"),
        CampoDef("cidade", "Cidade", "text", obrigatorio=True),
        CampoDef("nome_contato_cliente", "Nome do Contato (Cliente)", "text", obrigatorio=True),
        CampoDef("cargo", "Cargo", "text", obrigatorio=True),
        CampoDef("setor_contato", "Setor", "text", obrigatorio=True),
        CampoDef("fone", "Fone", "tel", obrigatorio=True, min_chars=10),
        CampoDef("email", "E-mail", "email", obrigatorio=True),
        # Descrição da Ocorrência
        CampoDef("produto", "Produto", "select", obrigatorio=True, opcoes=_PRODUTOS),
        CampoDef("lote", "Lote", "text", obrigatorio=True, min_chars=13),
        CampoDef("descricao_situacao", "Descrição da ocorrência", "textarea", obrigatorio=True),
        # Anexos (Fotos e/ou Vídeos) reaproveitam o upload padrão de anexos do
        # chamado (já presente em todo o portal) — não modelado como campo aqui.
    ),
    CAT_VISITA: (
        CampoDef("cliente_visitado", "Cliente a ser visitado", "text", obrigatorio=True),
        CampoDef("cidade", "Cidade", "text", obrigatorio=True),
        CampoDef("estado", "Estado", "text", obrigatorio=True),
        CampoDef(
            "regiao_cliente", "Região do cliente", "select",
            obrigatorio=True, opcoes=_REGIOES,
        ),
        CampoDef(
            "produtos_utilizados", "Quais produtos Bondmann o cliente utiliza?", "textarea",
            obrigatorio=True,
        ),
        CampoDef(
            "ocorrencia_anterior",
            "O cliente possui ocorrência anterior registrada?",
            "select",
            obrigatorio=True,
            opcoes=("Sim", "Não"),
        ),
        CampoDef(
            "detalhe_ocorrencia_anterior",
            "Caso tenha histórico de ocorrência, favor detalhar o ocorrido",
            "textarea",
        ),
        CampoDef(
            "objetivo_visita",
            "Objetivo da visita (descreva o objetivo de forma detalhada)",
            "textarea",
            obrigatorio=True,
        ),
    ),
    CAT_ANALISE: (
        CampoDef(
            "unidade_entrega",
            "Unidade de entrega da amostra (O envio das amostras é de "
            "responsabilidade do solicitante)",
            "select",
            obrigatorio=True,
            opcoes=_UNIDADES,
        ),
        CampoDef("identificacao_cliente", "Identificação do cliente", "text", obrigatorio=True),
        CampoDef(
            "descricao_amostra",
            "Descrição completa da amostra (produto, lote, diluição, aspecto e outras "
            "características pertinentes)",
            "textarea",
            obrigatorio=True,
        ),
        CampoDef(
            "analises_solicitadas",
            "Análises solicitadas (pode ser selecionado mais de um item)",
            "checkbox_multi",
            obrigatorio=True,
            opcoes=(
                "Determinação de pH",
                "Determinação de densidade",
                "Determinação de índice de refração (Grau Brix)",
                "Determinação de características de prevenção à corrosão de "
                "lubrificantes de refrigeração misturados com água",
                "Determinação de contaminação microbiana por dip slide test",
                "Determinação de reserva alcalina",
                "Determinação de pontos de acidez",
                "Outra (especificar no objetivo das análises, sendo sua realização "
                "condicionada à estrutura disponível e à aprovação prévia do "
                "Departamento Químico)",
            ),
        ),
        CampoDef(
            "objetivo_analises",
            "Objetivo das análises (objetivos e resultados esperados, conforme "
            "selecionado no item anterior)",
            "textarea",
            obrigatorio=True,
        ),
    ),
    CAT_DESENVOLVIMENTO: (
        CampoDef(
            "objetivo_desenvolvimento",
            "Objetivo do Desenvolvimento",
            "textarea",
            obrigatorio=True,
            ajuda="Objetivo da solicitação: produto desejado, finalidade, função ou "
            "problema a ser resolvido.",
        ),
        CampoDef(
            "justificativa",
            "Justificativa (tamanho do mercado, clientes potenciais, tipo de "
            "solução/aplicação, etc.)",
            "textarea",
            obrigatorio=True,
            ajuda="Por que a Bondmann teria interesse em realizar a solicitação? Qual "
            "o mercado/clientes que o novo produto vai atender? Qual o problema que o "
            "novo produto vai resolver? Que tipo de aplicação teria o novo produto?",
        ),
        CampoDef(
            "mercado_alvo",
            "Mercado-alvo",
            "textarea",
            obrigatorio=True,
            ajuda="Quais os clientes e/ou consumidores potenciais para este tipo de "
            "produto a ser desenvolvido? Quem vai comprar e usar este novo produto?",
        ),
        CampoDef(
            "concorrencia",
            "Concorrência (empresas, produtos similares, preços, etc.)",
            "textarea",
            obrigatorio=True,
            ajuda="Que empresas vendem produtos similares? Quais são os produtos "
            "similares? Dentre estes, quais servem como referência de qualidade e "
            "desempenho? Indicar preços praticados no mercado e quantidades de "
            "unidades vendidas.",
        ),
        CampoDef(
            "diferenciais",
            "Principais diferenciais a serem explorados",
            "textarea",
            obrigatorio=True,
            ajuda="Que diferenciais o produto desenvolvido pela Bondmann deve ter "
            "para facilitar a entrada no mercado-alvo e vencer a concorrência?",
        ),
    ),
}


def eh_categoria_quimico(nome: str | None) -> bool:
    """A categoria (por nome) tem um layout dinâmico do Químico?"""
    return bool(nome) and nome in CAMPOS_POR_CATEGORIA


def campos_da_categoria(nome: str | None) -> tuple[CampoDef, ...]:
    """Campos definidos para a categoria, ou tupla vazia se não for do Químico."""
    if not nome:
        return ()
    return CAMPOS_POR_CATEGORIA.get(nome, ())


def validar_payload(
    nome_categoria: str | None, dados: dict[str, list[str]]
) -> tuple[bool, str | None, dict[str, Any]]:
    """Valida as respostas dos campos dinâmicos de uma categoria do Químico.

    ``dados`` mapeia ``name -> lista de valores brutos`` como submetidos (um
    campo normal chega como lista de 1 item; um ``checkbox_multi`` pode chegar
    com 0..N). Retorna ``(ok, erro, limpo)``: ``limpo`` só contém as chaves
    conhecidas do schema (defesa em profundidade contra campos forjados no
    POST) — valor ``str`` para a maioria dos tipos, ``list[str]`` para
    ``checkbox_multi``. Categoria sem layout (não-Químico) ⇒ ``(True, None, {})``.
    """
    campos = campos_da_categoria(nome_categoria)
    if not campos:
        return True, None, {}

    limpo: dict[str, Any] = {}
    for campo in campos:
        brutos = dados.get(campo.name) or []
        if campo.tipo == "checkbox_multi":
            marcados = [v.strip() for v in brutos if v.strip()]
            invalidas = [v for v in marcados if v not in campo.opcoes]
            if invalidas:
                return False, f'Opção inválida no campo "{campo.label}".', {}
            if campo.obrigatorio and not marcados:
                return False, f'Selecione ao menos uma opção em "{campo.label}".', {}
            if marcados:
                limpo[campo.name] = marcados
            continue

        valor = (brutos[0] if brutos else "").strip()
        if not valor:
            if campo.obrigatorio:
                return False, f'Preencha o campo "{campo.label}".', {}
            continue  # opcional vazio: não grava chave
        if campo.tipo == "select" and valor not in campo.opcoes:
            return False, f'Opção inválida no campo "{campo.label}".', {}
        if campo.tipo == "date":
            try:
                date.fromisoformat(valor)
            except ValueError:
                return False, f'Data inválida no campo "{campo.label}".', {}
        if campo.tipo == "number":
            try:
                int(valor)
            except ValueError:
                return False, f'Valor numérico inválido no campo "{campo.label}".', {}
        if campo.tipo == "email" and not _EMAIL_RE.match(valor):
            return False, f'E-mail inválido no campo "{campo.label}".', {}
        if campo.min_chars and len(valor) < campo.min_chars:
            return (
                False,
                f'O campo "{campo.label}" precisa de pelo menos {campo.min_chars} caracteres.',
                {},
            )
        limpo[campo.name] = valor
    return True, None, limpo


def valores_para_template(
    nome_categoria: str | None, dados: dict[str, list[str]]
) -> dict[str, Any]:
    """Normaliza os valores brutos submetidos para prefill no template Jinja.

    Espelha o formato de ``validar_payload`` (``str`` normal / ``list[str]``
    para ``checkbox_multi``), mas SEM validar — usado só para reexibir o que o
    usuário digitou quando o formulário volta com erro."""
    campos = campos_da_categoria(nome_categoria)
    resultado: dict[str, Any] = {}
    for campo in campos:
        brutos = dados.get(campo.name) or []
        if campo.tipo == "checkbox_multi":
            resultado[campo.name] = [v for v in brutos if v]
        elif brutos:
            resultado[campo.name] = brutos[0]
    return resultado


def rotular(nome_categoria: str | None, dados: dict[str, Any]) -> list[tuple[str, str]]:
    """Pares ``(label, valor)`` para exibição, na ordem do schema.

    Usa o schema da categoria para rotular as chaves de ``dados_formulario``.
    Valores ``list`` (``checkbox_multi``) são juntados com "; ". Chaves
    presentes em ``dados`` mas ausentes do schema (ex.: campo removido depois
    de gravado) são anexadas ao final com o próprio ``name`` como rótulo, para
    não sumir com dado histórico."""
    if not dados:
        return []

    def _texto(valor: Any) -> str:
        return "; ".join(valor) if isinstance(valor, list) else str(valor)

    campos = campos_da_categoria(nome_categoria)
    vistos: set[str] = set()
    pares: list[tuple[str, str]] = []
    for campo in campos:
        if campo.name in dados and dados[campo.name]:
            pares.append((campo.label, _texto(dados[campo.name])))
            vistos.add(campo.name)
    for chave, valor in dados.items():
        if chave not in vistos and valor:
            pares.append((chave, _texto(valor)))
    return pares

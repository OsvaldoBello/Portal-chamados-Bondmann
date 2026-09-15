"""Formulários estruturados das subcategorias de acesso da TI (F1, 2026-09-14).

Governado por `plano_md_mestre_automacao_acessos.md` (Seção 4). As duas
subcategorias já existem no catálogo da TI desde a migration 0026 —
**"Usuários e Acessos" → "Criação de Novo Usuário"** e **"Desligamento /
Bloqueio de Acesso"** — e passam a ter um layout de campos que espelha 1:1 o
formulário interativo da automação de criação/desligamento (`main.py` do
projeto `Automação/`: `handle_create_user` e `handle_offboard_user`). É esse
`dados_formulario` que, na F2, vira o `payload` do job executado pelo worker.

Regras vindas do plano:
- **D3 — quem vê o layout:** só autores do RH, da TI ou com papel ADMIN
  (`autor_pode_usar_layout`). Fora disso a subcategoria continua com o
  formulário livre (Assunto/Descrição) e o `campo__*` é ignorado no POST.
- **Campos condicionais** (`visivel_se`): o `perfil` escolhido decide quais
  perguntas existem — Região/Dispositivo WMW só para Representante (e Região
  para Supervisor), Cargo/Papel/Setor/Licenças SAP só para Colaborador Interno.
- **O que NÃO vira campo:** grupos M365, times UBD, senha inicial, código de
  usuário SAP, ramal SIP — tudo derivado pela automação (Seção 4.4).
- Assunto/Descrição são ocultados na tela e derivados aqui
  (`titulo_e_descricao_automaticos`), como no precedente do Químico.

As opções de `select` são gravadas pelo RÓTULO exibido (ex.: ``"082-ARARAQUARA"``,
``"Representante Comercial (Externo)"``) — legível no detalhe do chamado e
aceito diretamente pelo `find_wmw_region` / pelo mapeamento de perfil da
automação, sem tabela de tradução de ids.
"""

from __future__ import annotations

from typing import Any

from app.domain.campos_dinamicos import CampoDef

# Nomes EXATOS das subcategorias no catálogo (migration 0026) — é por eles que
# o registro (`formularios_dinamicos.py`) casa a subcategoria escolhida com o
# layout. Categoria-mãe conferida junto para não colidir com uma subcategoria
# homônima de outro departamento.
DEPARTAMENTO_TI = "TI"
CAT_USUARIOS_E_ACESSOS = "Usuários e Acessos"
SUB_CRIACAO_USUARIO = "Criação de Novo Usuário"
SUB_DESLIGAMENTO = "Desligamento / Bloqueio de Acesso"

# Setores de origem cujos autores enxergam o layout (D3). Comparação pelo nome
# do departamento do PERFIL (`perfis.departamento_id` → `departamentos.nome`),
# nunca pelo campo "Setor" livre do formulário (mesmo motivo de
# `PortalService.representante_pode_marketing`).
_SETORES_AUTORIZADOS = {"rh", "ti"}

DOMINIO_CORPORATIVO = "bondmann.com.br"

# Perfis de usuário — rótulos idênticos aos do menu da automação (`main.py`),
# que decide o perfil por substring ("Representante" / "Colaborador" / resto ⇒
# Supervisor). Manter os textos: mudar aqui exige mudar o mapeamento lá.
PERFIL_REPRESENTANTE = "Representante Comercial (Externo)"
PERFIL_INTERNO = "Colaborador Interno (Escritório/Fábrica)"
PERFIL_SUPERVISOR = "Supervisor / Liderança de Equipe"
_PERFIS = (PERFIL_REPRESENTANTE, PERFIL_INTERNO, PERFIL_SUPERVISOR)

_DISPOSITIVOS_WMW = ("IOS (iPhone / iPad)", "ANDROID (Celular / Tablet)", "SIMULADOR (PC / Windows)")

# Papéis no Portal de Chamados (só Colaborador Interno escolhe; Representante e
# Supervisor recebem CLIENTE + setor fixo na automação — spec 2026-09-10 dela).
_PAPEIS_PORTAL = (
    "Funcionário (abre chamados)",
    "Operador de setor (atende a fila)",
    "Admin/líder de setor (relatórios + fila, se houver)",
)

# Licenças SAP Business One. A disponibilidade real é consultada ao vivo pela
# automação na execução (o portal não alcança o SAP — plano, Seção 0.1);
# aqui é só a escolha.
_LICENCAS_SAP = ("PROFESSIONAL", "CRM", "FINANCEIRA", "LOGISTICA")

# Regiões comerciais do WMW Vendas — extraídas do dropdown real do portal WMW
# (`config/wmw_regions.json` do projeto `Automação/`, 2026-09-14), só as
# entradas ``NNN-NOME`` (o dump também traz gerentes/equipes/dispositivos,
# que não são regiões). É a lista do Químico (`formularios_quimico._REGIOES`)
# mais 092, 095, 124 e 165, sem "VENDA DIRETA". Mantida separada de propósito:
# a fonte é outra (WMW, não o Microsoft Forms do Químico) e é ela que a
# automação valida.
REGIOES_WMW: tuple[str, ...] = (
    "001-COLOMBO", "002-SAO MIGUEL DO OESTE", "003-CURITIBA", "004-CATANDUVA", "005-CHAPECO",
    "006-IGREJINHA", "007-GRAVATAI", "008-SAO LEOPOLDO", "009-BENTO GONCALVES",
    "010-JARAGUA DO SUL", "011-PANAMBI", "013-CACHOEIRA DO SUL", "015-CASCAVEL", "016-CRICIUMA",
    "018-PATO BRANCO", "019-LAGES", "020-JOINVILLE", "021-SOROCABA", "022-CAXIAS DO SUL",
    "023-PALHOCA", "024-LAJEADO", "025-LITORAL", "026-BAGE", "027-ERECHIM",
    "028-SP-NORTE/OESTE", "029-BARUERI", "031-BAURU", "033-POA (ZONA NORTE)", "034-BLUMENAU",
    "035-SANTA CRUZ DO SUL", "037-PRESIDENTE PRUDENTE", "038-SANTA MARIA", "039-PONTA GROSSA",
    "040-SANTA ROSA", "041-PELOTAS", "042-MONTENEGRO", "043-SAO JOSE DO RIO PRETO",
    "044-VIDEIRA", "045-MARINGA", "046-BRUSQUE", "047-LONDRINA", "048-INDAIATUBA",
    "049-ARAUCARIA", "051-LIMEIRA", "053-RIBEIRAO PRETO", "054-CANOAS", "056-MOGI MIRIM",
    "057-VACARIA", "059-TOLEDO", "060-SAO JOAO DA BOA VISTA", "062-CIANORTE", "063-APUCARANA",
    "064-RIO CLARO", "065-CONTAGEM", "066-BELO HORIZONTE", "069-DIVINOPOLIS", "071-MARILIA",
    "072-NOVO HAMBURGO", "073-ARACATUBA", "075-POCOS DE CALDAS", "077-PASSO FUNDO",
    "079-SAO JOSE DOS PINHAIS", "080-APARECIDA DO NORTE", "081-JABOTICABAL", "082-ARARAQUARA",
    "083-SIQUEIRA CAMPOS", "084-UBERLANDIA", "085-FRANCA", "086-NITEROI", "087-SP-LESTE",
    "088-CAMPOS DOS GOYTACAZES", "089-PIRACICABA", "090-DUQUE DE CAXIAS", "091-GUARAPUAVA",
    "092-MONTE ALTO", "093-SAO JOSE DOS CAMPOS", "094-PARANAGUA", "095-MONTES CLAROS",
    "097-BRAGANCA PAULISTA", "098-JUNDIAI", "099-UBERABA", "100-FOZ DO IGUACU", "101-UMUARAMA",
    "102-FLORIANOPOLIS", "103-BOTUCATU", "104-ASSIS", "105-OURINHOS", "106-FRANCISCO BELTRAO",
    "124-SAO PEDRO", "125-BAIXADA SANTISTA", "126-PORTO FERREIRA", "127-BETIM", "128-SUMARE",
    "129-RIO DE JANEIRO", "130-SETE LAGOAS", "131-SP-SUL", "132-SP-ABCD", "134-JUIZ DE FORA",
    "136-VALE DO ACO", "137-VOLTA REDONDA", "138-NOVA FRIBURGO", "144-CATALAO", "145-ANAPOLIS",
    "146-CRISTALINA", "147-BRASILIA", "150-FREDERICO WESTPHALEN", "151-ALEGRETE",
    "152-PORTO UNIAO", "163-GOIANIA", "164-ITUMBIARA", "165-PARACATU",
    "166-CONSELHEIRO LAFAIETE", "167-BARBACENA", "168-CURVELO", "169-VARGINHA",
    "170-PATOS DE MINAS", "173-RIO DO SUL",
)

_SO_COMERCIAIS = ("perfil", (PERFIL_REPRESENTANTE, PERFIL_SUPERVISOR))
_SO_REPRESENTANTE = ("perfil", (PERFIL_REPRESENTANTE,))
_SO_INTERNO = ("perfil", (PERFIL_INTERNO,))


def campos_criacao(setores_portal: tuple[str, ...]) -> tuple[CampoDef, ...]:
    """Layout de "Criação de Novo Usuário". ``setores_portal`` = nomes dos
    departamentos ativos do catálogo (vem do banco, por isso é parâmetro e não
    constante) — é o "Setor/Departamento do Colaborador" no Portal de Chamados."""
    return (
        CampoDef("nome_completo", "Nome completo do colaborador", "text", obrigatorio=True, min_chars=5),
        CampoDef(
            "email", "E-mail corporativo", "email", obrigatorio=True,
            dominio_email=DOMINIO_CORPORATIVO, placeholder="nome.sobrenome@bondmann.com.br",
            ajuda="Padrão: primeiro nome + ponto + último sobrenome (preenchido automaticamente "
            "a partir do nome; ajuste se necessário).",
        ),
        CampoDef("perfil", "Perfil do usuário", "select", obrigatorio=True, opcoes=_PERFIS),
        CampoDef("telefone", "Telefone de contato (DDD + número)", "tel", obrigatorio=True, min_chars=10),
        CampoDef(
            "data_inicio", "Data de início", "date",
            ajuda="Opcional. Os acessos são criados a partir dessa data; em branco = assim que aprovado.",
        ),
        CampoDef(
            "gestor_email", "E-mail do gestor direto", "email",
            dominio_email=DOMINIO_CORPORATIVO,
            ajuda="Opcional. Usado como líder no UBD Learning.rocks.",
        ),
        # --- Colaborador Interno ---
        CampoDef(
            "cargo", "Cargo / função", "text", obrigatorio=True, visivel_se=_SO_INTERNO,
            placeholder="Ex.: Assessor de Laboratório, Assistente Financeira",
            ajuda="Define automaticamente os times do UBD e os grupos do Microsoft 365.",
        ),
        CampoDef(
            "portal_papel", "Papel no Portal de Chamados", "select", obrigatorio=True,
            opcoes=_PAPEIS_PORTAL, visivel_se=_SO_INTERNO,
        ),
        CampoDef(
            "portal_setor", "Setor / departamento do colaborador", "select", obrigatorio=True,
            opcoes=setores_portal, visivel_se=_SO_INTERNO,
        ),
        CampoDef(
            "licencas_sap", "Licenças SAP Business One", "checkbox_multi",
            opcoes=_LICENCAS_SAP, visivel_se=_SO_INTERNO,
            ajuda="Deixe em branco se o colaborador não usa o SAP. A disponibilidade é "
            "conferida pela TI na execução.",
        ),
        # --- Representante / Supervisor ---
        CampoDef(
            "regiao_wmw", "Região comercial (WMW / SAP)", "select", obrigatorio=True,
            opcoes=REGIOES_WMW, visivel_se=_SO_COMERCIAIS,
        ),
        CampoDef(
            "dispositivo_wmw", "Dispositivo do app WMW", "select", obrigatorio=True,
            opcoes=_DISPOSITIVOS_WMW, visivel_se=_SO_REPRESENTANTE,
        ),
        CampoDef(
            "observacoes", "Observações para a TI", "textarea",
            ajuda="Opcional. Qualquer detalhe que não se encaixe nos campos acima.",
        ),
    )


CAMPOS_DESLIGAMENTO: tuple[CampoDef, ...] = (
    CampoDef(
        "email", "E-mail corporativo do colaborador", "email", obrigatorio=True,
        dominio_email=DOMINIO_CORPORATIVO, placeholder="nome.sobrenome@bondmann.com.br",
    ),
    CampoDef("nome_completo", "Nome completo do colaborador", "text", obrigatorio=True, min_chars=5),
    CampoDef("perfil", "Perfil do usuário", "select", obrigatorio=True, opcoes=_PERFIS),
    CampoDef(
        "data_desligamento", "Data do desligamento", "date", obrigatorio=True,
        ajuda="Os acessos são bloqueados a partir dessa data.",
    ),
    CampoDef(
        "regiao", "Região comercial a desvincular", "select", obrigatorio=True,
        opcoes=REGIOES_WMW, visivel_se=_SO_COMERCIAIS,
        ajuda="A região é transferida para RH2020 no SAP.",
    ),
    CampoDef("motivo", "Motivo do desligamento", "text", obrigatorio=True, placeholder="Encerramento de Contrato de Trabalho"),
    CampoDef(
        "encaminhar_para", "Encaminhar os e-mails recebidos para", "email", obrigatorio=True,
        dominio_email=DOMINIO_CORPORATIVO, placeholder="pedidos@bondmann.com.br",
        ajuda="Caixa que passa a receber as mensagens do colaborador desligado.",
    ),
    CampoDef(
        "observacoes", "Observações para a TI", "textarea",
        ajuda="Opcional. Qualquer detalhe que não se encaixe nos campos acima.",
    ),
)


def autor_pode_usar_layout(perfil: dict[str, Any] | None) -> bool:
    """D3: o layout estruturado só é servido (e aceito no POST) para autores
    do RH, da TI ou com papel ADMIN — os demais abrem a subcategoria com o
    formulário livre. ``perfil`` é o dict de ``ChamadosRepo.perfil``."""
    if not perfil:
        return False
    if (perfil.get("role") or "").upper() == "ADMIN":
        return True
    return (perfil.get("departamento") or "").strip().lower() in _SETORES_AUTORIZADOS


def eh_subcategoria_acessos(
    departamento: str | None, categoria: str | None, subcategoria: str | None
) -> bool:
    return (
        (departamento or "").strip() == DEPARTAMENTO_TI
        and (categoria or "").strip() == CAT_USUARIOS_E_ACESSOS
        and (subcategoria or "").strip() in (SUB_CRIACAO_USUARIO, SUB_DESLIGAMENTO)
    )


def perfil_curto(valor: str | None) -> str:
    """Rótulo curto do perfil para o Assunto ("Representante", "Interno", "Supervisor")."""
    if valor == PERFIL_REPRESENTANTE:
        return "Representante"
    if valor == PERFIL_INTERNO:
        return "Colaborador Interno"
    if valor == PERFIL_SUPERVISOR:
        return "Supervisor"
    return ""


def titulo_e_descricao_automaticos(
    subcategoria: str | None, dados: dict[str, Any]
) -> tuple[str, str]:
    """Deriva (Assunto, Descrição) a partir das respostas — a tela esconde os
    dois campos para estas subcategorias. ``dados`` é o retorno limpo da
    validação. Subcategoria fora deste módulo ⇒ ``("", "")``."""
    nome = str(dados.get("nome_completo") or "").strip()
    perfil = perfil_curto(str(dados.get("perfil") or ""))
    if subcategoria == SUB_CRIACAO_USUARIO:
        titulo = "Criação de usuário"
        partes = [
            f"Solicitação de criação de acessos para {nome or '(nome não informado)'}",
            f"Perfil: {perfil or '—'}",
            f"E-mail: {dados.get('email') or '—'}",
        ]
        if dados.get("data_inicio"):
            partes.append(f"Início: {dados['data_inicio']}")
    elif subcategoria == SUB_DESLIGAMENTO:
        titulo = "Desligamento de acessos"
        partes = [
            f"Solicitação de bloqueio de acessos de {nome or '(nome não informado)'}",
            f"Perfil: {perfil or '—'}",
            f"E-mail: {dados.get('email') or '—'}",
            f"Data do desligamento: {dados.get('data_desligamento') or '—'}",
        ]
    else:
        return "", ""
    if nome:
        titulo = f"{titulo} — {nome}"
    if perfil:
        titulo = f"{titulo} ({perfil})"
    obs = str(dados.get("observacoes") or "").strip()
    if obs:
        partes += ["", f"Observações: {obs}"]
    return titulo[:160], "\n".join(partes)

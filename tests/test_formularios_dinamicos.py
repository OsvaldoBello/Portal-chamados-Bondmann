"""Formulários dinâmicos por subcategoria — motor genérico, layouts de acessos
da TI e registro (F1 da automação de acessos, 2026-09-14).

Unidade pura (sem app, sem banco): `app/domain/campos_dinamicos.py`,
`app/domain/formularios_acessos.py` e `app/domain/formularios_dinamicos.py`.
O fluxo HTTP (cascade HTMX, POST de abertura, gate D3 na rota) está em
`tests/test_portal.py` (seção "Abertura estruturada da TI").
"""

from __future__ import annotations

import pytest

from app.domain import formularios_acessos as ac
from app.domain.campos_dinamicos import (
    CampoDef,
    campo_visivel,
    campos_visiveis,
    rotular_campos,
    validar_campos,
    valores_para_template,
)
from app.domain.formularios_dinamicos import layout_para, rotular_chamado
from app.domain.formularios_quimico import CAT_OCORRENCIA

SETORES = ("TI", "RH", "Financeiro")


def _perfil(departamento="RH", role="CLIENTE"):
    return {"id": "u1", "role": role, "departamento": departamento}


def _criacao_interno(**over):
    base = {
        "nome_completo": ["Maria da Silva"],
        "email": ["maria.silva@bondmann.com.br"],
        "perfil": [ac.PERFIL_INTERNO],
        "telefone": ["51999998888"],
        "cargo": ["Assistente Financeira"],
        "portal_papel": ["Funcionário (abre chamados)"],
        "portal_setor": ["Financeiro"],
        "licencas_sap": ["PROFESSIONAL", "CRM"],
    }
    base.update(over)
    return base


def _criacao_representante(**over):
    base = {
        "nome_completo": ["João Pedro Souza"],
        "email": ["joao.souza@bondmann.com.br"],
        "perfil": [ac.PERFIL_REPRESENTANTE],
        "telefone": ["11988887777"],
        "regiao_wmw": ["082-ARARAQUARA"],
        "dispositivo_wmw": ["IOS (iPhone / iPad)"],
    }
    base.update(over)
    return base


def _desligamento(**over):
    base = {
        "email": ["joao.souza@bondmann.com.br"],
        "nome_completo": ["João Pedro Souza"],
        "perfil": [ac.PERFIL_REPRESENTANTE],
        "data_desligamento": ["2026-09-30"],
        "regiao": ["082-ARARAQUARA"],
        "motivo": ["Encerramento de Contrato de Trabalho"],
        "encaminhar_para": ["pedidos@bondmann.com.br"],
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------
# Motor genérico: campo condicional
# --------------------------------------------------------------------------
_CAMPOS_COND = (
    CampoDef("tipo", "Tipo", "select", obrigatorio=True, opcoes=("A", "B")),
    CampoDef("so_a", "Só para A", "text", obrigatorio=True, visivel_se=("tipo", ("A",))),
    CampoDef("so_b", "Só para B", "text", visivel_se=("tipo", ("B",))),
)


def test_campo_condicional_invisivel_nao_e_obrigatorio_nem_gravado():
    ok, erro, limpo = validar_campos(_CAMPOS_COND, {"tipo": ["B"], "so_a": ["forjado"]})
    assert ok, erro
    assert limpo == {"tipo": "B"}  # `so_a` ignorado mesmo tendo vindo no POST


def test_campo_condicional_visivel_exige_preenchimento():
    ok, erro, _ = validar_campos(_CAMPOS_COND, {"tipo": ["A"]})
    assert not ok
    assert "Só para A" in erro


def test_campo_visivel_le_o_controlador_bruto_ou_limpo():
    campo = _CAMPOS_COND[1]
    assert campo_visivel(campo, {"tipo": ["A"]})
    assert campo_visivel(campo, {"tipo": "A"})
    assert not campo_visivel(campo, {"tipo": []})
    assert not campo_visivel(campo, {})
    assert [c.name for c in campos_visiveis(_CAMPOS_COND, {"tipo": ["B"]})] == ["tipo", "so_b"]


def test_email_com_dominio_obrigatorio():
    campos = (CampoDef("email", "E-mail", "email", obrigatorio=True, dominio_email="bondmann.com.br"),)
    ok, erro, _ = validar_campos(campos, {"email": ["fulano@gmail.com"]})
    assert not ok and "@bondmann.com.br" in erro
    ok, _, limpo = validar_campos(campos, {"email": ["Fulano@Bondmann.com.br"]})
    assert ok and limpo["email"] == "Fulano@Bondmann.com.br"  # caixa preservada


def test_prefill_inclui_condicionais_inativos():
    dados = valores_para_template(_CAMPOS_COND, {"tipo": ["B"], "so_a": ["x"]})
    assert dados == {"tipo": "B", "so_a": "x"}


def test_rotular_campos_ordem_do_schema_e_chaves_orfas():
    pares = rotular_campos(_CAMPOS_COND, {"so_b": "b", "tipo": "B", "antigo": "z"})
    assert pares == [("Tipo", "B"), ("Só para B", "b"), ("antigo", "z")]


# --------------------------------------------------------------------------
# Layouts de acessos da TI
# --------------------------------------------------------------------------
def test_criacao_interno_valida_e_ignora_campos_comerciais():
    campos = ac.campos_criacao(SETORES)
    dados = _criacao_interno(regiao_wmw=["082-ARARAQUARA"], dispositivo_wmw=["IOS (iPhone / iPad)"])
    ok, erro, limpo = validar_campos(campos, dados)
    assert ok, erro
    assert limpo["licencas_sap"] == ["PROFESSIONAL", "CRM"]
    assert limpo["portal_setor"] == "Financeiro"
    assert "regiao_wmw" not in limpo and "dispositivo_wmw" not in limpo


def test_criacao_interno_exige_cargo_papel_e_setor():
    campos = ac.campos_criacao(SETORES)
    for faltante in ("cargo", "portal_papel", "portal_setor"):
        ok, erro, _ = validar_campos(campos, _criacao_interno(**{faltante: [""]}))
        assert not ok, faltante
        assert erro


def test_criacao_representante_exige_regiao_e_dispositivo_e_dispensa_cargo():
    campos = ac.campos_criacao(SETORES)
    ok, erro, limpo = validar_campos(campos, _criacao_representante())
    assert ok, erro
    assert limpo["regiao_wmw"] == "082-ARARAQUARA"
    assert "cargo" not in limpo
    ok, erro, _ = validar_campos(campos, _criacao_representante(regiao_wmw=[""]))
    assert not ok and "Região" in erro
    ok, erro, _ = validar_campos(campos, _criacao_representante(regiao_wmw=["999-NADA"]))
    assert not ok and "Opção inválida" in erro


def test_criacao_supervisor_pede_regiao_mas_nao_dispositivo():
    campos = ac.campos_criacao(SETORES)
    dados = _criacao_representante(perfil=[ac.PERFIL_SUPERVISOR], dispositivo_wmw=[""])
    ok, erro, limpo = validar_campos(campos, dados)
    assert ok, erro
    assert limpo["regiao_wmw"] == "082-ARARAQUARA"
    assert "dispositivo_wmw" not in limpo


def test_criacao_email_fora_do_dominio_e_recusado():
    ok, erro, _ = validar_campos(
        ac.campos_criacao(SETORES), _criacao_representante(email=["joao@gmail.com"])
    )
    assert not ok and "@bondmann.com.br" in erro


def test_desligamento_valida_e_regiao_so_para_comerciais():
    ok, erro, limpo = validar_campos(ac.CAMPOS_DESLIGAMENTO, _desligamento())
    assert ok, erro
    assert limpo["regiao"] == "082-ARARAQUARA"
    ok, erro, limpo = validar_campos(
        ac.CAMPOS_DESLIGAMENTO, _desligamento(perfil=[ac.PERFIL_INTERNO], regiao=[""])
    )
    assert ok, erro
    assert "regiao" not in limpo
    ok, erro, _ = validar_campos(ac.CAMPOS_DESLIGAMENTO, _desligamento(data_desligamento=["30/09/2026"]))
    assert not ok and "Data inválida" in erro


def test_titulo_e_descricao_automaticos_criacao_e_desligamento():
    titulo, descricao = ac.titulo_e_descricao_automaticos(
        ac.SUB_CRIACAO_USUARIO,
        {"nome_completo": "Maria da Silva", "perfil": ac.PERFIL_INTERNO,
         "email": "maria.silva@bondmann.com.br", "observacoes": "Começa dia 1º"},
    )
    assert titulo == "Criação de usuário — Maria da Silva (Colaborador Interno)"
    assert "maria.silva@bondmann.com.br" in descricao and "Começa dia 1º" in descricao

    titulo, descricao = ac.titulo_e_descricao_automaticos(
        ac.SUB_DESLIGAMENTO,
        {"nome_completo": "João Pedro Souza", "perfil": ac.PERFIL_REPRESENTANTE,
         "email": "joao.souza@bondmann.com.br", "data_desligamento": "2026-09-30"},
    )
    assert titulo == "Desligamento de acessos — João Pedro Souza (Representante)"
    assert "2026-09-30" in descricao
    assert ac.titulo_e_descricao_automaticos("Outra", {}) == ("", "")


def test_regioes_wmw_sao_unicas_e_no_formato_codigo_nome():
    assert len(ac.REGIOES_WMW) == len(set(ac.REGIOES_WMW)) == 117
    assert all(r[:3].isdigit() and r[3] == "-" for r in ac.REGIOES_WMW)
    assert "082-ARARAQUARA" in ac.REGIOES_WMW and "127-BETIM" in ac.REGIOES_WMW


# --------------------------------------------------------------------------
# Gate D3 e registro
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "perfil, esperado",
    [
        (_perfil("RH"), True),
        (_perfil("TI"), True),
        (_perfil("rh "), True),
        (_perfil("Financeiro", role="ADMIN"), True),
        (_perfil("Financeiro"), False),
        (_perfil("Representantes"), False),
        (_perfil(None), False),
        (None, False),
    ],
)
def test_autor_pode_usar_layout(perfil, esperado):
    assert ac.autor_pode_usar_layout(perfil) is esperado


def test_layout_para_acessos_resolve_por_subcategoria_da_ti():
    layout = layout_para(
        departamento="TI", categoria=ac.CAT_USUARIOS_E_ACESSOS,
        subcategoria=ac.SUB_CRIACAO_USUARIO, perfil_autor=_perfil("RH"), setores_portal=SETORES,
    )
    assert layout is not None and layout.origem == "acessos"
    assert layout.oculta_assunto and not layout.oculta_prioridade
    assert layout.sugere_email_de == "nome_completo"
    setor = next(c for c in layout.campos if c.name == "portal_setor")
    assert setor.opcoes == SETORES

    desl = layout_para(
        departamento="TI", categoria=ac.CAT_USUARIOS_E_ACESSOS,
        subcategoria=ac.SUB_DESLIGAMENTO, perfil_autor=_perfil("TI"),
    )
    assert desl is not None and desl.chave == ac.SUB_DESLIGAMENTO and desl.sugere_email_de == ""


def test_layout_para_nega_autor_fora_do_gate_e_outras_escolhas():
    comum = dict(departamento="TI", categoria=ac.CAT_USUARIOS_E_ACESSOS, subcategoria=ac.SUB_CRIACAO_USUARIO)
    assert layout_para(**comum, perfil_autor=_perfil("Financeiro")) is None
    # mesma subcategoria (nome) em outro departamento não conta
    assert layout_para(**{**comum, "departamento": "RH"}, perfil_autor=_perfil("RH")) is None
    assert layout_para(**{**comum, "subcategoria": "Redefinição de Senha"}, perfil_autor=_perfil("RH")) is None
    assert layout_para(departamento="TI", categoria="Hardware", subcategoria=None, perfil_autor=_perfil("RH")) is None
    # categoria homônima da do Químico em outro departamento não herda o layout
    assert layout_para(departamento="TI", categoria=CAT_OCORRENCIA, subcategoria=None, perfil_autor=_perfil("RH")) is None


def test_layout_para_quimico_continua_por_categoria_sem_gate():
    layout = layout_para(
        departamento="Dpto Químico", categoria=CAT_OCORRENCIA, subcategoria=None,
        perfil_autor=_perfil("Representantes"),
    )
    assert layout is not None and layout.origem == "quimico"
    assert layout.oculta_assunto and layout.oculta_prioridade
    assert layout.titulo_e_descricao({"nome_empresa_cliente": "X", "descricao_situacao": "Y"}) == (
        f"{CAT_OCORRENCIA} — X", "Y",
    )


def test_rotular_chamado_por_subcategoria_e_por_categoria():
    pares = rotular_chamado(
        ac.CAT_USUARIOS_E_ACESSOS, ac.SUB_CRIACAO_USUARIO,
        {"perfil": ac.PERFIL_INTERNO, "nome_completo": "Maria", "licencas_sap": ["CRM"]},
    )
    assert pares[0] == ("Nome completo do colaborador", "Maria")
    assert ("Licenças SAP Business One", "CRM") in pares
    assert rotular_chamado(CAT_OCORRENCIA, None, {"cidade": "Canoas"}) == [("Cidade", "Canoas")]
    assert rotular_chamado("Hardware", "Outros", {"x": "1"}) == [("x", "1")]
    assert rotular_chamado("Hardware", "Outros", None) == []

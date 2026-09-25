"""Formulários dinâmicos por subcategoria — motor genérico, layouts de acessos
da TI e registro (F1 da automação de acessos, 2026-09-14).

Unidade pura (sem app, sem banco): `app/domain/campos_dinamicos.py`,
`app/domain/formularios_acessos.py` e `app/domain/formularios_dinamicos.py`.
O fluxo HTTP (cascade HTMX, POST de abertura, gate D3 na rota) está em
`tests/test_portal.py` (seção "Abertura estruturada da TI").
"""

from __future__ import annotations

import pytest

from app.domain import formularios_acessos as ac, gerencias_internas as gi, vagas_comerciais as vc
from app.domain.campos_dinamicos import (
    VALOR_CHECKBOX_MARCADO,
    CampoDef,
    campo_visivel,
    campos_visiveis,
    rotular_campos,
    validar_campos,
    valores_para_template,
)
from app.domain.formularios_dinamicos import Layout, layout_para, rotular_chamado
from app.domain.formularios_quimico import CAT_OCORRENCIA
from app.templating import templates

SETORES = ("TI", "RH", "Financeiro")

# TEST DATA: Checkbox field
_CHECK = (CampoDef("urgente", "Urgência", "checkbox"),)


def test_checkbox_marcado_grava_sim_e_desmarcado_nao_grava():
    ok, erro, limpo = validar_campos(_CHECK, {"urgente": ["Sim"]})
    assert ok, erro
    assert limpo == {"urgente": VALOR_CHECKBOX_MARCADO}
    ok, erro, limpo = validar_campos(_CHECK, {})
    assert ok and limpo == {}


def test_checkbox_obrigatorio_desmarcado_e_recusado():
    ok, erro, _ = validar_campos((CampoDef("aceite", "Aceite", "checkbox", obrigatorio=True),), {})
    assert not ok and 'Marque o campo "Aceite"' in erro


def test_partial_renderiza_checkbox_marcado_e_desmarcado():
    tpl = templates.env.get_template("portal/_campos_dinamicos.html")
    layout = Layout(origem="acessos", chave="teste", campos=_CHECK)
    html = tpl.render(layout=layout, dados_form={"urgente": "Sim"})
    assert 'type="checkbox" name="campo__urgente" value="Sim"' in html and "checked" in html
    html = tpl.render(layout=layout, dados_form={})
    assert 'name="campo__urgente"' in html and "checked" not in html


def _perfil(departamento="RH", role="CLIENTE"):
    return {"id": "u1", "role": role, "departamento": departamento}


def _criacao_interno(**over):
    base = {
        "nome_completo": ["Maria da Silva"],
        "email": ["maria.silva@bondmann.com.br"],
        "perfil": [ac.PERFIL_INTERNO],
        "telefone": ["51999998888"],
        "gerencia_interna": [gi.OPCOES[0]],
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


def test_obrigatorio_se_exige_o_campo_so_na_condicao():
    campos = (
        CampoDef("tipo", "Tipo", "select", obrigatorio=True, opcoes=("A", "B")),
        CampoDef("detalhe", "Detalhe", "text", obrigatorio_se=("tipo", ("B",))),
    )
    assert validar_campos(campos, {"tipo": ["A"]})[0]
    ok, erro, _ = validar_campos(campos, {"tipo": ["B"]})
    assert not ok and "Detalhe" in erro


def test_obrigatorio_se_ignora_controlador_invisivel():
    # Controlador oculto (outro perfil) pode chegar no POST com valor antigo;
    # não pode tornar obrigatório um campo de outro contexto.
    campos = (
        CampoDef("perfil", "Perfil", "select", obrigatorio=True, opcoes=("X", "Y")),
        CampoDef("tipo", "Tipo", "select", opcoes=("A", "B"), visivel_se=("perfil", ("X",))),
        CampoDef("detalhe", "Detalhe", "text", obrigatorio_se=("tipo", ("B",))),
    )
    assert validar_campos(campos, {"perfil": ["Y"], "tipo": ["B"]})[0]
    assert not validar_campos(campos, {"perfil": ["X"], "tipo": ["B"]})[0]


def test_gerencias_internas_mapeiam_para_o_email_do_gerente():
    assert gi.email_do_gerente("PCP / Recebimento / Expedição — Elias Kirsten") == "elias@bondmann.com.br"
    assert gi.email_do_gerente(gi.OUTRA) is None and len(gi.GERENCIAS) == 8


def test_criacao_interno_exige_gerencia_e_email_so_com_outra():
    base = {k: v for k, v in _criacao_interno().items() if k != "gerencia_interna"}
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), base)
    assert not ok and "Gerência responsável" in erro
    ok, erro, limpo = validar_campos(ac.campos_criacao(SETORES), {**base, "gerencia_interna": [gi.OPCOES[0]]})
    assert ok, erro
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), {**base, "gerencia_interna": [gi.OUTRA]})
    assert not ok and "gestor" in erro.lower()
    ok, erro, limpo = validar_campos(
        ac.campos_criacao(SETORES),
        {**base, "gerencia_interna": [gi.OUTRA], "gestor_email": ["chefe.x@bondmann.com.br"]},
    )
    assert ok, erro
    assert limpo["gestor_email"] == "chefe.x@bondmann.com.br"


def test_gestor_email_nao_existe_para_representante():
    ok, erro, limpo = validar_campos(
        ac.campos_criacao(SETORES), _criacao_representante(gestor_email=["chefe.x@bondmann.com.br"])
    )
    assert ok, erro
    assert "gestor_email" not in limpo and "gerencia_interna" not in limpo


def test_catalogo_de_vagas_do_sap():
    codigos = [c for c, _ in vc.EQUIPES]
    assert "RH2018" in codigos and "RH2090" in codigos and "RH2040" not in codigos  # RH2040 inativo
    assert len(codigos) == len(set(codigos)) == 21
    assert vc.GERENCIAS == (("RH2005", "GERENTE SP"), ("RH2033", "GERENTE MG/RJ"))
    assert vc.vaga_do_rotulo("RH2018 — EQUIPE SP 1") == {"tipo": "EQUIPE", "codigo": "RH2018", "nome": "EQUIPE SP 1"}
    assert vc.vaga_do_rotulo("RH2005 — GERENTE SP")["tipo"] == "GERENCIA"
    assert vc.vaga_do_rotulo("qualquer coisa") is None


def test_criacao_supervisor_pede_equipe_e_nao_regiao():
    dados = _criacao_representante(perfil=[ac.PERFIL_SUPERVISOR], regiao_wmw=[""], dispositivo_wmw=[""],
                                   equipe=["RH2018 — EQUIPE SP 1"])
    ok, erro, limpo = validar_campos(ac.campos_criacao(SETORES), dados)
    assert ok, erro
    assert limpo["equipe"] == "RH2018 — EQUIPE SP 1" and "regiao_wmw" not in limpo
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), {**dados, "equipe": [""]})
    assert not ok and "Equipe correspondente" in erro


def test_criacao_supervisor_com_equipe_fora_do_catalogo_e_recusada():
    dados = _criacao_representante(perfil=[ac.PERFIL_SUPERVISOR], regiao_wmw=[""], dispositivo_wmw=[""],
                                   equipe=["RH2040 — EQUIPE RJ 1"])
    ok, erro, _ = validar_campos(ac.campos_criacao(SETORES), dados)
    assert not ok and "Opção inválida" in erro


def test_criacao_gerente_pede_gerencia():
    dados = _criacao_representante(perfil=[ac.PERFIL_GERENTE], regiao_wmw=[""], dispositivo_wmw=[""],
                                   gerencia=["RH2033 — GERENTE MG/RJ"])
    ok, erro, limpo = validar_campos(ac.campos_criacao(SETORES), dados)
    assert ok, erro
    assert limpo["gerencia"] == "RH2033 — GERENTE MG/RJ" and "equipe" not in limpo


def test_desligamento_supervisor_pede_equipe():
    ok, erro, limpo = validar_campos(
        ac.CAMPOS_DESLIGAMENTO,
        _desligamento(perfil=[ac.PERFIL_SUPERVISOR], regiao=[""], equipe=["RH2021 — EQUIPE MG 2"]),
    )
    assert ok, erro
    assert limpo["equipe"] == "RH2021 — EQUIPE MG 2" and "regiao" not in limpo


def test_titulo_e_descricao_citam_a_vaga():
    titulo, descricao = ac.titulo_e_descricao_automaticos(
        ac.SUB_CRIACAO_USUARIO,
        {"nome_completo": "Carla Prado", "perfil": ac.PERFIL_SUPERVISOR, "equipe": "RH2018 — EQUIPE SP 1"},
    )
    assert titulo == "Criação de usuário — Carla Prado (Supervisor · EQUIPE SP 1)"
    assert "Vaga: RH2018 — EQUIPE SP 1" in descricao


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


def test_desligamento_motivo_e_opcional():
    """Pedido do gestor (2026-09-15): o RH nem sempre sabe/quer informar o motivo."""
    ok, erro, limpo = validar_campos(ac.CAMPOS_DESLIGAMENTO, _desligamento(motivo=[""]))
    assert ok, erro
    assert "motivo" not in limpo


def test_desligamento_urgencia_e_opcional_e_grava_sim():
    ok, erro, limpo = validar_campos(ac.CAMPOS_DESLIGAMENTO, _desligamento(urgente=["Sim"]))
    assert ok, erro
    assert limpo["urgente"] == VALOR_CHECKBOX_MARCADO
    ok, erro, limpo = validar_campos(ac.CAMPOS_DESLIGAMENTO, _desligamento())
    assert ok, erro
    assert "urgente" not in limpo


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
    assert "Urgência" not in descricao

    _, descricao = ac.titulo_e_descricao_automaticos(
        ac.SUB_DESLIGAMENTO,
        {"nome_completo": "João Pedro Souza", "perfil": ac.PERFIL_REPRESENTANTE,
         "email": "joao.souza@bondmann.com.br", "data_desligamento": "2026-09-30", "urgente": "Sim"},
    )
    assert "Urgência: SIM — executar assim que a TI aprovar (ignora a data)" in descricao
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

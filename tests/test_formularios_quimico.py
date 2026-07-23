"""Testes do schema de formulários dinâmicos do Químico (validação e rotulagem).

Os campos vieram dos 3 Microsoft Forms reais do setor (2026-07-21). ``dados``
para ``validar_payload``/``valores_para_template`` é sempre ``dict[str,
list[str]]`` — mesmo formato de ``request.form().multi_items()`` agrupado por
chave, necessário porque ``checkbox_multi`` manda várias entradas com o mesmo
nome (ver app/routes/portal.py)."""

from __future__ import annotations

from app.domain.formularios_quimico import (
    CAT_ANALISE,
    CAT_OCORRENCIA,
    CAT_VISITA,
    campos_da_categoria,
    eh_categoria_quimico,
    rotular,
    titulo_e_descricao_automaticos,
    validar_payload,
    valores_para_template,
)


def _payload_ocorrencia_valido(**over: list[str]) -> dict[str, list[str]]:
    """Payload mínimo válido para o Registro de Ocorrência (todos os obrigatórios)."""
    base = {
        "representante": ["Zeca"],
        "regiao": ["001-COLOMBO"],
        "supervisor": ["CHRISTIAN ALVES SEVERO"],
        "gerente": ["ANDRE LUIZ MANDELLI"],
        "nome_empresa_cliente": ["Cliente X"],
        "cidade": ["Canoas"],
        "nome_contato_cliente": ["Fulano"],
        "cargo": ["Comprador"],
        "setor_contato": ["Compras"],
        "fone": ["51999998888"],
        "email": ["fulano@cliente.com"],
        "tipo_ocorrencia": ["PRODUTO"],
        "produto": ["ALKARES"],
        "lote": ["LOTE1234567890"],
        "descricao_situacao": ["Vazou no piso"],
        "produto_funcao_especifica": ["Sim"],
        "ambiente_apropriado": ["Sim"],
        "operadores_treinados": ["Sim"],
        "procedimentos_medicao": ["Sim"],
        "ocorrencia_pontual": ["Sim"],
        "processos_anteriores_afetam": ["Não"],
        "outras_informacoes": ["Não"],
    }
    base.update(over)
    return base


def test_categorias_conhecidas_tem_campos():
    for nome in (CAT_OCORRENCIA, CAT_VISITA, CAT_ANALISE):
        assert eh_categoria_quimico(nome)
        assert campos_da_categoria(nome), f"{nome} deveria ter campos"


def test_categoria_desconhecida_sem_layout():
    assert not eh_categoria_quimico("Folha de Pagamento")
    assert not eh_categoria_quimico(None)
    assert campos_da_categoria("Inexistente") == ()


def test_categoria_nao_quimico_passa_sem_dados():
    ok, erro, limpo = validar_payload("Categoria de outro setor", {"campo": ["x"]})
    assert ok and erro is None
    assert limpo == {}  # sem layout ⇒ não grava nada


def test_ocorrencia_payload_valido():
    ok, erro, limpo = validar_payload(CAT_OCORRENCIA, _payload_ocorrencia_valido())
    assert ok and erro is None
    assert limpo["nome_empresa_cliente"] == "Cliente X"
    assert limpo["produto_funcao_especifica"] == "Sim"
    # Campo de detalhe opcional não preenchido não entra no resultado.
    assert "produto_funcao_especifica_detalhe" not in limpo


def test_ocorrencia_obrigatorio_vazio_falha():
    dados = _payload_ocorrencia_valido(representante=[""])
    ok, erro, limpo = validar_payload(CAT_OCORRENCIA, dados)
    assert not ok
    assert erro and "Representante" in erro
    assert limpo == {}


def test_ocorrencia_lote_curto_falha_min_chars():
    dados = _payload_ocorrencia_valido(lote=["ABC"])
    ok, erro, _ = validar_payload(CAT_OCORRENCIA, dados)
    assert not ok
    assert "Lote" in (erro or "") and "13" in (erro or "")


def test_ocorrencia_fone_curto_falha_min_chars():
    dados = _payload_ocorrencia_valido(fone=["123"])
    ok, erro, _ = validar_payload(CAT_OCORRENCIA, dados)
    assert not ok
    assert "Fone" in (erro or "")


def test_ocorrencia_email_invalido_falha():
    dados = _payload_ocorrencia_valido(email=["nao-e-email"])
    ok, erro, _ = validar_payload(CAT_OCORRENCIA, dados)
    assert not ok
    assert "E-mail" in (erro or "")


def test_ocorrencia_causal_opcao_invalida_falha():
    dados = _payload_ocorrencia_valido(produto_funcao_especifica=["Talvez"])
    ok, erro, _ = validar_payload(CAT_OCORRENCIA, dados)
    assert not ok
    assert "função específica" in (erro or "")


def test_ocorrencia_causal_com_detalhe():
    dados = _payload_ocorrencia_valido(
        produto_funcao_especifica=["Outra"],
        produto_funcao_especifica_detalhe=["Usado fora do recomendado"],
    )
    ok, _, limpo = validar_payload(CAT_OCORRENCIA, dados)
    assert ok
    assert limpo["produto_funcao_especifica"] == "Outra"
    assert limpo["produto_funcao_especifica_detalhe"] == "Usado fora do recomendado"


def test_visita_tecnica_payload_valido_e_campos_forjados_ignorados():
    dados = {
        "solicitante": ["Zeca"],
        "cliente_visitado": ["Cliente Y"],
        "cidade": ["Indaiatuba"],
        "regiao_cliente": ["001-COLOMBO"],
        "unidade_atendimento": ["Matriz Canoas/RS"],
        "produtos_utilizados": ["Óleo X"],
        "ocorrencia_anterior": ["Não"],
        "objetivo_visita": ["Inspeção de rotina"],
        "campo_forjado": ["não deveria entrar"],
    }
    ok, erro, limpo = validar_payload(CAT_VISITA, dados)
    assert ok and erro is None
    assert "campo_forjado" not in limpo
    assert limpo["unidade_atendimento"] == "Matriz Canoas/RS"
    assert "detalhe_ocorrencia_anterior" not in limpo  # opcional vazio


def test_visita_tecnica_unidade_invalida_falha():
    dados = {
        "solicitante": ["Zeca"], "cliente_visitado": ["Y"], "cidade": ["X"],
        "regiao_cliente": ["001-COLOMBO"], "unidade_atendimento": ["Filial Fantasma"],
        "produtos_utilizados": ["Óleo"], "ocorrencia_anterior": ["Sim"],
        "objetivo_visita": ["Visita"],
    }
    ok, erro, _ = validar_payload(CAT_VISITA, dados)
    assert not ok
    assert "Unidade Bondmann de Atendimento" in (erro or "")


def _payload_analise_valido(**over: list[str]) -> dict[str, list[str]]:
    base = {
        "identificacao_solicitante": ["Zeca"],
        "unidade_entrega": ["Matriz Canoas/RS"],
        "identificacao_cliente": ["Cliente Z"],
        "descricao_amostra": ["Óleo, lote 123, aspecto turvo"],
        "analises_solicitadas": ["Determinação de pH", "Determinação de densidade"],
        "objetivo_analises": ["Confirmar especificação"],
    }
    base.update(over)
    return base


def test_analise_laboratorial_checkbox_multi_valido():
    ok, erro, limpo = validar_payload(CAT_ANALISE, _payload_analise_valido())
    assert ok and erro is None
    assert limpo["analises_solicitadas"] == ["Determinação de pH", "Determinação de densidade"]


def test_analise_laboratorial_checkbox_multi_vazio_obrigatorio_falha():
    dados = _payload_analise_valido(analises_solicitadas=[])
    ok, erro, _ = validar_payload(CAT_ANALISE, dados)
    assert not ok
    assert "Análises solicitadas" in (erro or "")


def test_analise_laboratorial_checkbox_multi_opcao_invalida_falha():
    dados = _payload_analise_valido(analises_solicitadas=["Opção forjada"])
    ok, erro, _ = validar_payload(CAT_ANALISE, dados)
    assert not ok
    assert "Análises solicitadas" in (erro or "")


def test_valores_para_template_normaliza_para_prefill():
    brutos = {
        "identificacao_solicitante": ["Zeca"],
        "analises_solicitadas": ["Determinação de pH", "Determinação de densidade"],
    }
    valores = valores_para_template(CAT_ANALISE, brutos)
    assert valores["identificacao_solicitante"] == "Zeca"
    assert valores["analises_solicitadas"] == ["Determinação de pH", "Determinação de densidade"]


def test_valores_para_template_categoria_desconhecida_vazio():
    assert valores_para_template("Inexistente", {"x": ["y"]}) == {}


def test_rotular_ordena_pelo_schema_e_preserva_extras():
    dados = {"descricao_situacao": "vazou", "cidade": "Canoas", "sobra": "x"}
    pares = rotular(CAT_OCORRENCIA, dados)
    labels = [label for label, _ in pares]
    assert labels.index("Cidade") < labels.index("Descrição da Situação")
    assert ("sobra", "x") in pares


def test_rotular_junta_lista_checkbox_multi_com_ponto_e_virgula():
    dados = {"analises_solicitadas": ["Determinação de pH", "Determinação de densidade"]}
    pares = rotular(CAT_ANALISE, dados)
    assert ("Análises solicitadas (pode ser selecionado mais de um item)",
            "Determinação de pH; Determinação de densidade") in pares


def test_rotular_dados_vazios():
    assert rotular(CAT_OCORRENCIA, {}) == []


def test_titulo_e_descricao_automaticos_ocorrencia():
    dados = {"nome_empresa_cliente": "Cliente X", "descricao_situacao": "Vazou no piso"}
    titulo, descricao = titulo_e_descricao_automaticos(CAT_OCORRENCIA, dados)
    assert titulo == "Registro de Ocorrência — Cliente X"
    assert descricao == "Vazou no piso"


def test_titulo_e_descricao_automaticos_visita():
    dados = {"cliente_visitado": "Cliente Y", "objetivo_visita": "Inspeção de rotina"}
    titulo, descricao = titulo_e_descricao_automaticos(CAT_VISITA, dados)
    assert titulo == "Solicitação de Visita Técnica — Cliente Y"
    assert descricao == "Inspeção de rotina"


def test_titulo_e_descricao_automaticos_analise():
    dados = {"identificacao_cliente": "Cliente Z", "descricao_amostra": "Óleo, lote 123"}
    titulo, descricao = titulo_e_descricao_automaticos(CAT_ANALISE, dados)
    assert titulo == "Solicitação de Análise Laboratorial — Cliente Z"
    assert descricao == "Óleo, lote 123"


def test_titulo_e_descricao_automaticos_sem_dados_cai_no_nome_da_categoria():
    titulo, descricao = titulo_e_descricao_automaticos(CAT_OCORRENCIA, {})
    assert titulo == CAT_OCORRENCIA
    assert descricao == CAT_OCORRENCIA


def test_titulo_e_descricao_automaticos_categoria_nao_quimico_vazio():
    assert titulo_e_descricao_automaticos("Categoria de outro setor", {"x": "y"}) == ("", "")
    assert titulo_e_descricao_automaticos(None, {}) == ("", "")

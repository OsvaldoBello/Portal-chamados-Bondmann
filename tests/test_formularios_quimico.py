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
        "produto": ["ALKARES"],
        "lote": ["LOTE1234567890"],
        "descricao_situacao": ["Vazou no piso"],
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
    assert limpo["descricao_situacao"] == "Vazou no piso"


def test_ocorrencia_obrigatorio_vazio_falha():
    dados = _payload_ocorrencia_valido(cidade=[""])
    ok, erro, limpo = validar_payload(CAT_OCORRENCIA, dados)
    assert not ok
    assert erro and "Cidade" in erro
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


def test_visita_tecnica_payload_valido_e_campos_forjados_ignorados():
    dados = {
        "cliente_visitado": ["Cliente Y"],
        "cidade": ["Indaiatuba"],
        "estado": ["SP"],
        "regiao_cliente": ["001-COLOMBO"],
        "produtos_utilizados": ["Óleo X"],
        "ocorrencia_anterior": ["Não"],
        "objetivo_visita": ["Inspeção de rotina"],
        "campo_forjado": ["não deveria entrar"],
    }
    ok, erro, limpo = validar_payload(CAT_VISITA, dados)
    assert ok and erro is None
    assert "campo_forjado" not in limpo
    assert limpo["estado"] == "SP"
    assert "detalhe_ocorrencia_anterior" not in limpo  # opcional vazio


def test_visita_tecnica_regiao_invalida_falha():
    dados = {
        "cliente_visitado": ["Y"], "cidade": ["X"], "estado": ["SP"],
        "regiao_cliente": ["Região Fantasma"],
        "produtos_utilizados": ["Óleo"], "ocorrencia_anterior": ["Sim"],
        "objetivo_visita": ["Visita"],
    }
    ok, erro, _ = validar_payload(CAT_VISITA, dados)
    assert not ok
    assert "Região do cliente" in (erro or "")


def _payload_analise_valido(**over: list[str]) -> dict[str, list[str]]:
    base = {
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
        "identificacao_cliente": ["Cliente Z"],
        "analises_solicitadas": ["Determinação de pH", "Determinação de densidade"],
    }
    valores = valores_para_template(CAT_ANALISE, brutos)
    assert valores["identificacao_cliente"] == "Cliente Z"
    assert valores["analises_solicitadas"] == ["Determinação de pH", "Determinação de densidade"]


def test_valores_para_template_categoria_desconhecida_vazio():
    assert valores_para_template("Inexistente", {"x": ["y"]}) == {}


def test_rotular_ordena_pelo_schema_e_preserva_extras():
    dados = {"descricao_situacao": "vazou", "cidade": "Canoas", "sobra": "x"}
    pares = rotular(CAT_OCORRENCIA, dados)
    labels = [label for label, _ in pares]
    assert labels.index("Cidade") < labels.index("Descrição da ocorrência")
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

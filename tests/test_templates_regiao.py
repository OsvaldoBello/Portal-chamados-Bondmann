from app.services.sap_regiao import RegiaoInfo
from app.templating import templates


def test_render_atendimento_com_regiao_unica():
    template = templates.get_template("workspace/atendimento.html")

    regiao = RegiaoInfo(
        tipo="representante",
        rotulo="057-VACARIA",
        regioes=["057-VACARIA"],
        detalhe="057-VACARIA",
        eh_multiplo=False,
    )

    html = template.render(
        chamado={
            "id": "1",
            "codigo": "BD-2026-00001",
            "titulo": "Teste",
            "descricao": "Desc",
            "cliente_nome": "Douglas Melo",
            "telefone_contato": "54996012120",
            "status": "EM_ATENDIMENTO",
            "prioridade": "URGENTE",
            "created_at": None,
            "limite_resolucao": None,
        },
        regiao_autor=regiao,
        perfil={"role": "ADMIN"},
        mensagens=[],
        observadores=[],
        combinados=[],
        candidatos_combinacao=[],
        operadores=[],
        departamentos=[],
        categorias_edit=[],
        subcategorias_edit=[],
        status_validos=[],
        prioridades=[],
        origem="",
    )
    assert "Região:" in html
    assert "057-VACARIA" in html


def test_render_atendimento_com_multiplas_regioes():
    template = templates.get_template("workspace/atendimento.html")

    regiao = RegiaoInfo(
        tipo="supervisor",
        rotulo="6 regiões vinculadas",
        regioes=["033-POA", "009-BENTO"],
        detalhe="033-POA, 009-BENTO",
        eh_multiplo=True,
    )

    html = template.render(
        chamado={
            "id": "1",
            "codigo": "BD-2026-00001",
            "titulo": "Teste",
            "descricao": "Desc",
            "cliente_nome": "Eliezer Jerke",
            "telefone_contato": "5199999999",
            "status": "EM_ATENDIMENTO",
            "prioridade": "URGENTE",
            "created_at": None,
            "limite_resolucao": None,
        },
        regiao_autor=regiao,
        perfil={"role": "ADMIN"},
        mensagens=[],
        observadores=[],
        combinados=[],
        candidatos_combinacao=[],
        operadores=[],
        departamentos=[],
        categorias_edit=[],
        subcategorias_edit=[],
        status_validos=[],
        prioridades=[],
        origem="",
    )
    assert "Região:" in html
    assert "6 regiões vinculadas" in html
    assert "033-POA, 009-BENTO" in html

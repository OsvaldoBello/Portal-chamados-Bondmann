"""Bateria adversarial do intake via WhatsApp — o que uma pessoa LEIGA faz.

Complementa `test_whatsapp_intake.py` (que cobre um bug real por teste, cada
um documentando o achado que o motivou). Aqui a lógica é outra: varrer
SISTEMATICAMENTE o espaço de respostas ruins — valor fora da lista, campo
omitido, valor de outro campo, string vazia, resposta repetida, eco, dict
poluído com chaves inventadas — contra TODAS as categorias e TODOS os campos
do formulário dinâmico do Químico, verificando os mesmos INVARIANTES em todos.

Motivação (pedido do gestor, 2026-08-24): os representantes que usam o canal
são leigos em tecnologia e vocabulário, e um erro pequeno gera estresse
grande — o padrão é "perfeito", não "funciona na maioria das vezes". Depois
de 12 bugs achados num único dia de teste ao vivo, cada um uma variação de
"o modelo esqueceu o que ele mesmo já resolveu" ou "o código só verificou
metade da condição", a cobertura passa a ser combinatória em vez de
caso-a-caso.

Os invariantes, válidos para TODA rodada de PERGUNTA:
  I1. nunca manda mensagem idêntica a outra já mandada na conversa;
  I2. nunca manda a resposta do próprio usuário de volta (eco);
  I3. nunca despeja lista de opções gigante (ilegível no WhatsApp);
  I4. nunca grava em `campos_formulario` chave fora do schema da categoria;
  I5. nunca grava valor de `select`/`checkbox_multi` fora da lista real;
  I6. nunca perde um campo já confirmado em rodada anterior;
  I7. a auditoria registra a pergunta REALMENTE enviada;
  I8. chamado só é criado com `validar_payload` (a mesma do Portal) aprovando.
"""

import json
from typing import Any

import pytest

from app.domain.formularios_quimico import (
    CAT_ANALISE,
    CAT_DESENVOLVIMENTO,
    CAT_OCORRENCIA,
    CAT_VISITA,
    campos_da_categoria,
    validar_payload,
)
from app.ia import whatsapp_intake
from tests.test_whatsapp_intake import (  # reuso do harness já existente
    _CATALOGO_QUIMICO,
    FakeConn,
    _saida_quimico,
    _settings,
    _valores_validos,
    ambiente,
)

_CATEGORIAS = (CAT_OCORRENCIA, CAT_VISITA, CAT_ANALISE, CAT_DESENVOLVIMENTO)


def _campos_com_opcoes(categoria: str):
    return [c for c in campos_da_categoria(categoria) if c.opcoes]


def _todos_os_campos(categoria: str):
    return list(campos_da_categoria(categoria))


def _checar_invariantes(
    conn: FakeConn, amb, categoria: str, confirmados_antes: dict[str, Any] | None = None
) -> None:
    """I1-I8 sobre o resultado de UMA rodada já processada."""
    assert conn.auditorias, "nenhuma rodada auditada"
    acao = conn.auditorias[0][2]
    resultado = json.loads(conn.auditorias[0][3])
    campos = resultado.get("campos_formulario") or {}

    enviadas = [c.args[1] for c in amb.responder.await_args_list]
    if enviadas:
        ultima = enviadas[-1]
        # I1 — nenhuma mensagem repetida nesta conversa.
        assert enviadas.count(ultima) == 1, f"I1: mensagem repetida: {ultima[:100]!r}"
        # I3 — nunca um muro de texto.
        assert len(ultima) <= 700, f"I3: mensagem de {len(ultima)} chars"
        # I2 — nunca eco da resposta do usuário.
        ultimo_usuario = next(
            (m["conteudo"] for m in reversed(conn.mensagens_acumuladas)
             if m.get("papel") == "usuario"),
            "",
        )
        if ultimo_usuario:
            assert ultima.strip().casefold() != str(ultimo_usuario).strip().casefold(), (
                f"I2: ecoou a resposta do usuário: {ultima[:80]!r}"
            )

    # I4 — nenhuma chave inventada.
    validos = {c.name for c in campos_da_categoria(categoria)}
    if validos:
        assert set(campos) <= validos, f"I4: chaves fora do schema: {set(campos) - validos}"

    # I5 — nenhum valor de lista fechada fora da lista.
    for campo in campos_da_categoria(categoria):
        valor = campos.get(campo.name)
        if not campo.opcoes or valor in (None, "", []):
            continue
        if campo.tipo == "select":
            assert valor in campo.opcoes, f"I5: {campo.name}={valor!r} fora da lista"
        elif campo.tipo == "checkbox_multi" and isinstance(valor, list):
            invalidos = [v for v in valor if v not in campo.opcoes]
            assert not invalidos, f"I5: {campo.name} contém {invalidos!r} fora da lista"

    # I6 — dado já confirmado nunca regride.
    for chave, valor in (confirmados_antes or {}).items():
        if valor in (None, "", []):
            continue
        assert campos.get(chave) not in (None, "", []), (
            f"I6: {chave}={valor!r} (confirmado antes) sumiu"
        )

    # I7 — auditoria bate com o enviado.
    if acao == "PERGUNTA" and enviadas:
        registradas = resultado.get("perguntas") or []
        if len(registradas) == 1:
            assert registradas[0] == enviadas[-1], (
                f"I7: gravou {str(registradas[0])[:60]!r}, enviou {enviadas[-1][:60]!r}"
            )

    # I8 — chamado só com formulário aprovado pela validação do Portal.
    # `dados_formulario` chega no formato "limpo" (escalar por chave); a
    # validação espera o formato bruto (`name -> list[str]`, como o multipart
    # do Portal), então converte com a MESMA função que o intake usa.
    if amb.criar.await_count:
        dados = amb.criar.await_args.kwargs.get("dados_formulario")
        if dados and campos_da_categoria(categoria):
            brutos = whatsapp_intake._campos_formulario_brutos(dados)
            ok, erro, _limpo = validar_payload(categoria, brutos)
            assert ok and not erro, f"I8: chamado criado com formulário inválido: {erro}"


# ---------------------------------------------------------------------------
# 1. Valor fora da lista, campo a campo, categoria a categoria
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "categoria,nome_campo",
    [(cat, c.name) for cat in _CATEGORIAS for c in _campos_com_opcoes(cat)],
)
async def test_valor_fora_da_lista_em_qualquer_campo(categoria, nome_campo):
    """Todo campo de lista fechada rejeita um valor inventado, sem gravar nada
    e sem quebrar nenhum invariante."""
    conn = FakeConn()
    campo = next(c for c in campos_da_categoria(categoria) if c.name == nome_campo)
    valores = _valores_validos(categoria)
    valores[nome_campo] = (
        ["Coisa Que Nao Existe"] if campo.tipo == "checkbox_multi" else "Coisa Que Nao Existe"
    )
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": f"Qual é {campo.label}?"},
        {"papel": "usuario", "conteudo": "coisa que nao existe"},
    ]
    saida = _saida_quimico(
        categoria, campos_formulario=valores, informacoes_suficientes=False,
        perguntas=["E o próximo?"],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        amb.criar.assert_not_awaited()
        _checar_invariantes(conn, amb, categoria)
        resultado = json.loads(conn.auditorias[0][3])
        assert nome_campo not in (resultado.get("campos_formulario") or {})


# ---------------------------------------------------------------------------
# 2. Valor de OUTRO campo (o erro "Bruno Tiara é gerente, não supervisor")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "categoria,campo_a,campo_b",
    [
        (cat, a.name, b.name)
        for cat in _CATEGORIAS
        for a in _campos_com_opcoes(cat)
        for b in _campos_com_opcoes(cat)
        if a.name != b.name and a.tipo == "select" and b.tipo == "select"
    ],
)
async def test_valor_de_outro_campo_nao_e_aceito(categoria, campo_a, campo_b):
    """Valor real da lista de B colocado no campo A: mesmo sendo uma string
    "válida" em algum lugar do formulário, não pode ser aceita em A."""
    campos = {c.name: c for c in campos_da_categoria(categoria)}
    valor_de_b = campos[campo_b].opcoes[0]
    if valor_de_b in campos[campo_a].opcoes:
        pytest.skip("listas compartilham esse valor — não é um caso de erro")

    conn = FakeConn()
    valores = _valores_validos(categoria)
    valores[campo_a] = valor_de_b
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": f"Qual é {campos[campo_a].label}?"},
        {"papel": "usuario", "conteudo": valor_de_b},
    ]
    saida = _saida_quimico(
        categoria, campos_formulario=valores, informacoes_suficientes=False,
        perguntas=["E agora?"],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        _checar_invariantes(conn, amb, categoria)
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["campos_formulario"].get(campo_a) != valor_de_b


# ---------------------------------------------------------------------------
# 3. Campo omitido pelo modelo (a pessoa respondeu, o modelo não reconheceu)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "categoria,nome_campo",
    [(cat, c.name) for cat in _CATEGORIAS for c in _campos_com_opcoes(cat)],
)
async def test_campo_omitido_nao_trava_a_conversa(categoria, nome_campo):
    """O modelo não preenche NEM erra — só ignora o campo. A conversa não pode
    virar mensagem genérica nem repetir; tem que pedir o campo certo."""
    campo = next(c for c in campos_da_categoria(categoria) if c.name == nome_campo)
    ordem = [c.name for c in campos_da_categoria(categoria)]
    anteriores = {
        c.name: _valores_validos(categoria)[c.name]
        for c in campos_da_categoria(categoria)
        if ordem.index(c.name) < ordem.index(nome_campo)
    }
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": categoria,
        "campos_formulario": anteriores,
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": f"Qual é {campo.label}?"},
        {"papel": "usuario", "conteudo": "sei la, nao lembro"},
    ]
    travada = _saida_quimico(
        categoria, campos_formulario=dict(anteriores), informacoes_suficientes=False,
        perguntas=[f"Qual é {campo.label}?"],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(travada, None, 100, 50), (travada, None, 90, 45)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        amb.criar.assert_not_awaited()
        _checar_invariantes(conn, amb, categoria, confirmados_antes=anteriores)
        resposta = amb.responder.await_args.args[1]
        assert resposta != whatsapp_intake._TEXTO_CONTINUAR_GENERICO


# ---------------------------------------------------------------------------
# 4. String vazia em campo nunca perguntado (o "dict poluído")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("categoria", _CATEGORIAS)
async def test_dict_poluido_com_vazios_nao_pula_campo_pendente(categoria):
    """O modelo devolve TODAS as chaves do schema, a maioria com "" — nenhuma
    delas pode contar como respondida, e o próximo campo pedido tem que ser o
    primeiro genuinamente pendente na ORDEM DO SCHEMA."""
    campos = campos_da_categoria(categoria)
    primeiro = campos[0]
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": categoria,
        "campos_formulario": {},
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": f"Qual é {primeiro.label}?"},
        {"papel": "usuario", "conteudo": "aham"},
    ]
    poluido = _saida_quimico(
        categoria,
        campos_formulario={c.name: "" for c in campos},
        informacoes_suficientes=False,
        perguntas=[f"Qual é {primeiro.label}?"],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(poluido, None, 100, 50), (poluido, None, 90, 45)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        amb.criar.assert_not_awaited()
        _checar_invariantes(conn, amb, categoria)
        resultado = json.loads(conn.auditorias[0][3])
        gravados = resultado.get("campos_formulario") or {}
        assert all(v in (None, "", []) for v in gravados.values()), (
            "vazio virou dado gravado"
        )


# ---------------------------------------------------------------------------
# 5. Eco e repetição, campo a campo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("categoria", _CATEGORIAS)
async def test_eco_da_resposta_do_usuario_nunca_chega_no_whatsapp(categoria):
    """O modelo devolve como `pergunta` a própria resposta da pessoa."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": categoria,
        "campos_formulario": {},
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": "Me conta o que aconteceu."},
        {"papel": "usuario", "conteudo": "o produto vazou no cliente"},
    ]
    eco = _saida_quimico(
        categoria, campos_formulario={}, informacoes_suficientes=False,
        perguntas=["o produto vazou no cliente"],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(eco, None, 100, 50), (eco, None, 90, 45)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        _checar_invariantes(conn, amb, categoria)


@pytest.mark.parametrize("categoria", _CATEGORIAS)
async def test_pergunta_identica_a_anterior_nunca_reenviada(categoria):
    """O modelo repete, palavra por palavra, a pergunta da rodada anterior."""
    conn = FakeConn()
    conn.rodada = 1
    texto = "Me conta o que aconteceu na ocorrência."
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": categoria,
        "campos_formulario": {},
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": texto},
        {"papel": "usuario", "conteudo": "ja falei"},
    ]
    repetida = _saida_quimico(
        categoria, campos_formulario={}, informacoes_suficientes=False, perguntas=[texto],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(repetida, None, 100, 50), (repetida, None, 90, 45)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        assert amb.responder.await_args.args[1] != texto
        _checar_invariantes(conn, amb, categoria)


# ---------------------------------------------------------------------------
# 6. Campo de topo (setor/categoria) já resolvido na própria rodada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "campo_topo,valor,frase",
    [
        ("setor", "TI", "Qual é o seu setor mesmo?"),
        ("setor", "Produção", "Só me diz de qual setor você é."),
        ("categoria", CAT_OCORRENCIA, "Qual é a categoria que você quer?"),
    ],
)
async def test_campo_de_topo_resolvido_nao_e_perguntado_de_novo(campo_topo, valor, frase):
    """O modelo preenche `setor`/`categoria` no JSON e mesmo assim pergunta por
    eles em linguagem natural."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "",
        "departamento": "Dpto Químico",
        "categoria": "",
        "campos_formulario": {},
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": "Preciso saber isso pra abrir certo."},
        {"papel": "usuario", "conteudo": valor},
    ]
    overrides: dict[str, Any] = {
        "campos_formulario": {},
        "informacoes_suficientes": False,
        "perguntas": [frase],
        "departamento": "Dpto Químico",
        "setor": "TI",
    }
    if campo_topo == "setor":
        overrides["setor"] = valor
        categoria_arg = ""
    else:
        categoria_arg = valor
    travada = _saida_quimico(categoria_arg, **overrides)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(travada, None, 100, 50), (travada, None, 90, 45)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        resposta = amb.responder.await_args.args[1]
        assert resposta != frase, "reenviou a pergunta sobre campo que já resolveu"


# ---------------------------------------------------------------------------
# 6b. Campo de texto livre nunca é acusado de "não reconhecido"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "categoria,nome_campo",
    [
        (cat, c.name)
        for cat in _CATEGORIAS
        for c in _todos_os_campos(cat)
        if not c.opcoes and c.tipo in ("text", "textarea")
    ],
)
async def test_campo_de_texto_livre_nunca_e_acusado_de_invalido(categoria, nome_campo):
    """Achado da simulação adversarial (2026-08-24): num campo de texto livre
    QUALQUER resposta é válida — não existe lista contra a qual "reconhecer".
    Dizer '"x" não é reconhecido' ali é mentira e gera loop de frustração (a
    pessoa repete a mesma coisa achando que digitou errado). A mensagem tem
    que pedir mais detalhe, nunca acusar a resposta de inválida."""
    campo = next(c for c in campos_da_categoria(categoria) if c.name == nome_campo)
    ordem = [c.name for c in campos_da_categoria(categoria)]
    anteriores = {
        c.name: _valores_validos(categoria)[c.name]
        for c in campos_da_categoria(categoria)
        if ordem.index(c.name) < ordem.index(nome_campo)
    }
    resposta_usuario = "é sobre o produto que vazou"
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": categoria,
        "campos_formulario": anteriores,
    }
    # A rodada anterior já usou o fallback determinístico pra ESTE campo —
    # é o que faz o código tentar a versão "enriquecida" com a tentativa.
    conn.mensagens_acumuladas = [
        {
            "papel": "assistente",
            "conteudo": f'Ainda preciso de "{whatsapp_intake._rotulo_chat(campo)}" '
            "pra completar o registro.",
        },
        {"papel": "usuario", "conteudo": resposta_usuario},
    ]
    travada = _saida_quimico(
        categoria, campos_formulario=dict(anteriores), informacoes_suficientes=False,
        perguntas=[f'Ainda preciso de "{whatsapp_intake._rotulo_chat(campo)}" '
                   "pra completar o registro."],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(travada, None, 100, 50), (travada, None, 90, 45)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        resposta = amb.responder.await_args.args[1]
        assert "não é reconhecido" not in resposta.lower()
        assert resposta_usuario not in resposta
        _checar_invariantes(conn, amb, categoria, confirmados_antes=anteriores)


# ---------------------------------------------------------------------------
# 7. Chaves inventadas pelo modelo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("categoria", _CATEGORIAS)
async def test_chaves_inventadas_nunca_viram_dado(categoria):
    """Variações inventadas de nome de campo não podem poluir o formulário."""
    conn = FakeConn()
    valores = _valores_validos(categoria)
    sujo = dict(valores) | {
        "campo_que_nao_existe": "x",
        "observacoes_gerais": "y",
        "regiao_do_cliente_completa": "z",
    }
    saida = _saida_quimico(categoria, campos_formulario=sujo)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        _checar_invariantes(conn, amb, categoria)


# ---------------------------------------------------------------------------
# 8. Caminho feliz — o formulário completo e válido SEMPRE vira chamado
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("categoria", _CATEGORIAS)
async def test_formulario_completo_sempre_cria_chamado(categoria):
    conn = FakeConn()
    saida = _saida_quimico(categoria, campos_formulario=_valores_validos(categoria))
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        amb.criar.assert_awaited_once()
        _checar_invariantes(conn, amb, categoria)


# ---------------------------------------------------------------------------
# 9. Um campo faltando de cada vez — nunca cria chamado incompleto
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "categoria,nome_campo",
    [
        (cat, c.name)
        for cat in _CATEGORIAS
        for c in _todos_os_campos(cat)
        if c.obrigatorio
    ],
)
async def test_campo_obrigatorio_faltando_nunca_cria_chamado(categoria, nome_campo):
    """Mesmo com o modelo afirmando `informacoes_suficientes: true`."""
    conn = FakeConn()
    valores = _valores_validos(categoria)
    del valores[nome_campo]
    saida = _saida_quimico(categoria, campos_formulario=valores)  # suficientes=True
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        amb.criar.assert_not_awaited()
        _checar_invariantes(conn, amb, categoria)


# ---------------------------------------------------------------------------
# 10. Valor curto demais em campo com min_chars
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "categoria,nome_campo",
    [
        (cat, c.name)
        for cat in _CATEGORIAS
        for c in _todos_os_campos(cat)
        if c.min_chars > 1
    ],
)
async def test_valor_curto_demais_nunca_cria_chamado(categoria, nome_campo):
    conn = FakeConn()
    valores = _valores_validos(categoria)
    valores[nome_campo] = "ab"
    saida = _saida_quimico(categoria, campos_formulario=valores)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")
        amb.criar.assert_not_awaited()
        _checar_invariantes(conn, amb, categoria)

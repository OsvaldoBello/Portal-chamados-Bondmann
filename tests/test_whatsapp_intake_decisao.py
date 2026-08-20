"""Decisão do intake WhatsApp (função pura) e extração de mensagens do webhook."""

from datetime import datetime

import pytest

from app.config import Settings
from app.domain.periodo import TZ_BR
from app.ia.schemas import SaidaWhatsAppIntake
from app.ia.whatsapp_intake import (
    _EMOJIS_CONFIRMACAO,
    _LEAD_INS_MULTIPLAS_PERGUNTAS,
    _mesclar_campos_confirmados,
    _pede_novo_chamado,
    _saudacao_horario,
    _texto_confirmacao,
    decidir_acao_intake,
    extrair_mensagens,
    montar_mensagens,
    texto_das_perguntas,
)


def _saida(**overrides) -> SaidaWhatsAppIntake:
    base = dict(
        informacoes_suficientes=True,
        confianca="ALTA",
        titulo="Impressora não imprime",
        descricao="A impressora do setor parou de imprimir hoje de manhã.",
        setor="Produção",
        departamento="TI",
        categoria="Equipamentos",
        subcategoria="Impressora",
        prioridade="MEDIA",
    )
    base.update(overrides)
    return SaidaWhatsAppIntake(**base)


def test_informacoes_suficientes_cria_chamado():
    assert decidir_acao_intake(_saida(), rodada=1, max_rodadas=4) == "CRIAR_CHAMADO"


def test_insuficiente_com_pergunta_pergunta():
    saida = _saida(
        informacoes_suficientes=False,
        perguntas=["Qual impressora apresentou o problema?"],
        titulo=None,
        descricao=None,
        departamento=None,
        categoria=None,
        subcategoria=None,
    )
    assert decidir_acao_intake(saida, rodada=1, max_rodadas=4) == "PERGUNTA"


def test_suficiente_sem_setor_pergunta_em_vez_de_criar():
    """`setor` é campo obrigatório do chamado e, por decisão de 2026-08-18, vem
    da conversa. Modelo que diz "suficiente" sem setor está errado — vira
    pergunta, nunca chamado sem dono declarado."""
    saida = _saida(setor=None, perguntas=["De qual setor você é?"])
    assert decidir_acao_intake(saida, rodada=1, max_rodadas=4) == "PERGUNTA"


def test_suficiente_sem_setor_e_sem_pergunta_encerra():
    assert (
        decidir_acao_intake(_saida(setor="  ", perguntas=[]), rodada=1, max_rodadas=4)
        == "ENCERRAR_SEM_CHAMADO"
    )


def test_teto_de_rodadas_encerra_em_vez_de_perguntar():
    """Sem isso o usuário ficaria num loop de 'não entendi, repete'."""
    saida = _saida(
        informacoes_suficientes=False,
        perguntas=["Pode detalhar melhor?"],
        titulo=None,
    )
    assert decidir_acao_intake(saida, rodada=4, max_rodadas=4) == "ENCERRAR_SEM_CHAMADO"


def test_insuficiente_sem_pergunta_encerra():
    saida = _saida(informacoes_suficientes=False, perguntas=["   "], titulo=None)
    assert decidir_acao_intake(saida, rodada=1, max_rodadas=4) == "ENCERRAR_SEM_CHAMADO"


def test_sem_saida_do_modelo_encerra():
    assert decidir_acao_intake(None, rodada=1, max_rodadas=4) == "ENCERRAR_SEM_CHAMADO"


def test_assunto_fora_do_escopo_encerra_mesmo_com_rodada_e_pergunta_disponiveis():
    """Achado do teste real do setor Brigadistas (2026-08-19): com o piloto
    restrito a poucos departamentos, insistir perguntando não cria um destino
    que não existe no catálogo — encerra na rodada 1, mesmo com pergunta
    formulada e longe do teto."""
    saida = _saida(
        informacoes_suficientes=False,
        assunto_fora_do_escopo=True,
        perguntas=["Isso afeta mais alguém no seu setor?"],  # ignorada de propósito
        setor="Brigadistas",
        departamento=None,
        categoria=None,
        subcategoria=None,
    )
    assert decidir_acao_intake(saida, rodada=1, max_rodadas=6) == "ENCERRAR_SEM_CHAMADO"


# --- texto_das_perguntas ---------------------------------------------------


def test_uma_pergunta_vai_crua():
    """A apresentação da primeira rodada cai aqui — numerar "1." ficaria bizarro."""
    assert texto_das_perguntas(["De qual setor você é?"]) == "De qual setor você é?"


def test_varias_perguntas_sao_numeradas():
    """A transição de abertura é escolhida pelo código (varia entre rodadas,
    2026-08-19 — achado da sessão: o modelo sozinho reciclava sempre a mesma
    frase de recapitulação), então o teste checa a lista numerada, não a
    string inteira."""
    texto = texto_das_perguntas(["Qual equipamento?", "Desde quando?", "Já tentou algo?"])
    lead_in, resto = texto.split("\n", 1)
    assert lead_in in _LEAD_INS_MULTIPLAS_PERGUNTAS
    assert resto == "1. Qual equipamento?\n2. Desde quando?\n3. Já tentou algo?"


def test_lead_in_nunca_aparece_com_pergunta_unica():
    """Só a rodada de apresentação (e um esclarecimento pontual) usa item
    único — não pode ganhar a introdução das perguntas múltiplas."""
    texto = texto_das_perguntas(["De qual setor você é?"])
    assert texto not in _LEAD_INS_MULTIPLAS_PERGUNTAS
    assert "\n" not in texto


def test_vazios_sao_descartados():
    assert texto_das_perguntas(["  ", "Qual equipamento?", ""]) == "Qual equipamento?"
    assert texto_das_perguntas([]) == ""
    assert texto_das_perguntas(["   "]) == ""


# --- _saudacao_horario -------------------------------------------------


@pytest.mark.parametrize(
    "hora,esperado",
    [
        (4, "Boa noite"),
        (5, "Bom dia"),
        (11, "Bom dia"),
        (12, "Boa tarde"),
        (17, "Boa tarde"),
        (18, "Boa noite"),
        (23, "Boa noite"),
        (0, "Boa noite"),
    ],
)
def test_saudacao_por_faixa_de_horario(hora, esperado):
    """Fronteiras do corte de senso comum (5h/12h/18h) — o modelo não tem
    relógio, então é essencial que o código acerte a faixa (2026-08-19)."""
    assert _saudacao_horario(datetime(2026, 8, 19, hora, 0, tzinfo=TZ_BR)) == esperado


def test_montar_mensagens_injeta_saudacao_no_turno_do_usuario():
    """O modelo não recebe relógio — a saudação certa vem pronta no prompt,
    calculada em Python, não "verificada" pelo modelo."""
    msgs = montar_mensagens([], catalogo=[], setores=["TI"])
    conteudo = msgs[1]["content"]
    assert "## Saudação atual" in conteudo
    assert any(s in conteudo for s in ("Bom dia", "Boa tarde", "Boa noite"))


def test_montar_mensagens_injeta_campos_ja_confirmados():
    """Achado em produção (2026-08-20): sem isso, o modelo às vezes reformula
    uma pergunta já respondida em rodada anterior. O setor/departamento já
    extraídos entram como fato pronto no turno do usuário, igual à saudação."""
    msgs = montar_mensagens(
        [], catalogo=[], setores=["TI"], campos_confirmados={"setor": "TI"}
    )
    conteudo = msgs[1]["content"]
    assert "## Dados já confirmados nesta conversa" in conteudo
    assert "Setor de quem está pedindo: TI" in conteudo


def test_montar_mensagens_sem_campos_confirmados_nao_injeta_secao():
    """Rodada 1 (nada extraído ainda) não deve mostrar a seção vazia."""
    msgs = montar_mensagens([], catalogo=[], setores=["TI"])
    assert "## Dados já confirmados" not in msgs[1]["content"]


# --- _mesclar_campos_confirmados ---------------------------------------


def test_mesclar_campos_confirmados_repoe_campo_omitido():
    """O modelo esqueceu de repetir `setor` nesta rodada — o valor
    confirmado na rodada anterior é reposto, nunca se perde."""
    saida = _saida(setor=None)
    mesclada = _mesclar_campos_confirmados(saida, {"setor": "TI"})
    assert mesclada.setor == "TI"


def test_mesclar_campos_confirmados_nao_sobrescreve_valor_novo():
    """A pessoa corrigiu o setor nesta rodada — o valor novo do modelo
    vence, o confirmado antigo não sobrescreve."""
    saida = _saida(setor="Produção")
    mesclada = _mesclar_campos_confirmados(saida, {"setor": "Compras"})
    assert mesclada.setor == "Produção"


def test_mesclar_campos_confirmados_sem_mudanca_devolve_o_mesmo_objeto():
    saida = _saida(setor="TI")
    assert _mesclar_campos_confirmados(saida, {"setor": "TI"}) is saida


# --- _texto_confirmacao -----------------------------------------------


def test_confirmacao_varia_o_emoji():
    """Achado da sessão 2026-08-19: o modelo repetia sempre o mesmo emoji na
    confirmação — aqui o texto é fixo em Python, então quem varia é o código."""
    settings = Settings(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
    )
    vistos = {
        _texto_confirmacao("BOND-2026-00999", "TI", "chamado-uuid", settings)
        for _ in range(40)
    }
    # 40 sorteios entre 5 opções: praticamente certo de ver mais de um emoji.
    assert len(vistos) > 1
    for texto in vistos:
        assert "BOND-2026-00999" in texto
        assert "/portal/chamados/chamado-uuid" in texto
        assert any(e in texto for e in _EMOJIS_CONFIRMACAO)


# --- extrair_mensagens -----------------------------------------------------


def _payload(*mensagens) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": {"messages": list(mensagens)}}]}],
    }


def test_extrai_texto():
    payload = _payload(
        {"id": "wamid.A", "from": "5551999998888", "type": "text", "text": {"body": "olá"}}
    )
    msgs = extrair_mensagens(payload)
    assert msgs == [
        {
            "wamid": "wamid.A",
            "telefone": "5551999998888",
            "tipo": "text",
            "corpo": "olá",
            "midia_id": None,
            "midia_nome": None,
        }
    ]


def test_extrai_imagem_com_legenda():
    payload = _payload(
        {
            "id": "wamid.B",
            "from": "5551999998888",
            "type": "image",
            "image": {"id": "midia-123", "caption": "olha o erro"},
        }
    )
    (msg,) = extrair_mensagens(payload)
    assert msg["midia_id"] == "midia-123"
    assert msg["corpo"] == "olha o erro"
    assert msg["midia_nome"] is None  # Meta não manda filename pra foto


def test_extrai_documento_com_nome_e_legenda():
    payload = _payload(
        {
            "id": "wamid.E",
            "from": "5551999998888",
            "type": "document",
            "document": {
                "id": "midia-456",
                "filename": "orcamento.pdf",
                "caption": "segue o orçamento",
            },
        }
    )
    (msg,) = extrair_mensagens(payload)
    assert msg["midia_id"] == "midia-456"
    assert msg["midia_nome"] == "orcamento.pdf"
    assert msg["corpo"] == "segue o orçamento"


def test_ignora_statuses_e_payload_vazio():
    """Eventos de entrega/leitura não são mensagens do usuário."""
    payload = {
        "entry": [{"changes": [{"value": {"statuses": [{"id": "wamid.X", "status": "read"}]}}]}]
    }
    assert extrair_mensagens(payload) == []
    assert extrair_mensagens(None) == []
    assert extrair_mensagens({}) == []


def test_mensagem_sem_id_ou_remetente_e_descartada():
    payload = _payload(
        {"from": "5551999998888", "type": "text", "text": {"body": "sem id"}},
        {"id": "wamid.C", "type": "text", "text": {"body": "sem from"}},
    )
    assert extrair_mensagens(payload) == []


def test_tipo_sem_suporte_entra_com_corpo_vazio():
    """Áudio/vídeo não somem em silêncio — viram turno vazio para a IA pedir texto."""
    payload = _payload({"id": "wamid.D", "from": "5551999998888", "type": "audio"})
    (msg,) = extrair_mensagens(payload)
    assert msg["tipo"] == "audio"
    assert msg["corpo"] == ""
    assert msg["midia_id"] is None


# --- Legenda pedindo chamado novo (bypass do anexo pós-criação) ------------


@pytest.mark.parametrize(
    "legenda",
    [
        "novo chamado: o ar-condicionado da sala 3 tá vazando",
        "Chamado Novo, não tem nada a ver com o outro",
        "isso aqui é OUTRO CHAMADO",
        "outro problema: a impressora pegou fogo",
        "quero abrir outro chamado pra isso",
        "  novo chamado  ",
    ],
)
def test_pede_novo_chamado_detecta_variacoes(legenda):
    assert _pede_novo_chamado(legenda) is True


@pytest.mark.parametrize(
    "legenda",
    ["", "segue a nota fiscal", "olha essa foto", "chamado", "novo"],
)
def test_pede_novo_chamado_nao_dispara_por_engano(legenda):
    assert _pede_novo_chamado(legenda) is False

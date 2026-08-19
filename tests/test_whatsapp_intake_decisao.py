"""Decisão do intake WhatsApp (função pura) e extração de mensagens do webhook."""

from app.ia.schemas import SaidaWhatsAppIntake
from app.ia.whatsapp_intake import (
    decidir_acao_intake,
    extrair_mensagens,
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


# --- texto_das_perguntas ---------------------------------------------------


def test_uma_pergunta_vai_crua():
    """A apresentação da primeira rodada cai aqui — numerar "1." ficaria bizarro."""
    assert texto_das_perguntas(["De qual setor você é?"]) == "De qual setor você é?"


def test_varias_perguntas_sao_numeradas():
    texto = texto_das_perguntas(["Qual equipamento?", "Desde quando?", "Já tentou algo?"])
    assert texto == "1. Qual equipamento?\n2. Desde quando?\n3. Já tentou algo?"


def test_vazios_sao_descartados():
    assert texto_das_perguntas(["  ", "Qual equipamento?", ""]) == "Qual equipamento?"
    assert texto_das_perguntas([]) == ""
    assert texto_das_perguntas(["   "]) == ""


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

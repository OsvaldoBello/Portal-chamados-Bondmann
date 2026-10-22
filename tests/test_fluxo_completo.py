"""Testes integrados de ponta a ponta dos fluxos alterados (Sprint 2026-10-22):
1. Trava de avaliação por setor (abertura na TI liberada mesmo com pendência no Marketing).
2. Usuário em cópia (observador) pode visualizar e enviar comentários no chamado.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from tests.test_portal import (
    UID,
    FakeRepo,
    _abertura_valida,
    _chamado,
    _csrf,
    portal_client,
)

# ==============================================================================
# FLUXO 1: TRAVA DE AVALIAÇÃO DE CHAMADOS VIGENTE APENAS PARA O PRÓPRIO SETOR
# ==============================================================================


def test_fluxo_completo_trava_avaliacao_por_setor():
    """Valida o fluxo completo de abertura:
    - O autor tem um chamado pendente de avaliação no MARKETING ('d3').
    - O autor pode abrir chamados para a TI ('d1') livremente.
    - O autor é travado/redirecionado caso tente abrir chamado para o MARKETING ('d3').
    """
    pendencia_mkt = {
        "id": "chamado-mkt-resolvido",
        "codigo": "BD-2026-00010",
        "titulo": "Material Gráfico",
        "departamento_id": "d3",
    }
    repo = FakeRepo(avaliacao_pendente=pendencia_mkt)

    with portal_client(repo) as client:
        # 1. Acesso à tela /novo sem especificar setor: NÃO é bloqueado
        resp_novo = client.get("/portal/chamados/novo")
        assert resp_novo.status_code == 200
        assert "Selecione o departamento" in resp_novo.text
        # A opção do Marketing traz o aviso visual de avaliação pendente
        assert "Marketing (avaliação pendente)" in resp_novo.text
        # A opção da TI está limpa e disponível
        assert ">TI<" in resp_novo.text or ">TI (" not in resp_novo.text

        # 2. Usuário escolhe "TI" no select -> cascade de categorias via HTMX
        resp_cat_ti = client.get("/portal/chamados/categorias?departamento_id=d1")
        assert resp_cat_ti.status_code == 200
        assert "HX-Redirect" not in resp_cat_ti.headers
        assert "Logística / Entrega" in resp_cat_ti.text

        # 3. Usuário envia formulário de abertura para a TI -> SUCESSO!
        token = _csrf(client)
        resp_criar_ti = client.post(
            "/portal/chamados",
            data=_abertura_valida(
                titulo="Computador não liga",
                descricao="Máquina travou completamente",
                departamento_id="d1",
                categoria_id="c1",
                subcategoria_id="s1",
                setor="TI",
                telefone_contato="11988887777",
                prioridade="ALTA",
            ),
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert resp_criar_ti.status_code == 303
        assert resp_criar_ti.headers["location"] == "/portal/chamados/novo-id"
        assert len(repo.criados) == 1
        assert repo.criados[0]["departamento_id"] == "d1"

        # 4. Agora, usuário tenta abrir diretamente com ?departamento_id=d3 (Marketing)
        resp_novo_mkt = client.get(
            "/portal/chamados/novo?departamento_id=d3", follow_redirects=False
        )
        assert resp_novo_mkt.status_code == 303
        assert (
            resp_novo_mkt.headers["location"]
            == "/portal/chamados/chamado-mkt-resolvido?avaliar_pendente=1"
        )

        # 5. Usuário seleciona "Marketing" no select -> HTMX recebe HX-Redirect imediato
        resp_cat_mkt = client.get("/portal/chamados/categorias?departamento_id=d3")
        assert (
            resp_cat_mkt.headers.get("HX-Redirect")
            == "/portal/chamados/chamado-mkt-resolvido?avaliar_pendente=1"
        )

        # 6. Usuário tenta forçar POST de criação para o Marketing -> Redirecionado para avaliar
        resp_criar_mkt = client.post(
            "/portal/chamados",
            data=_abertura_valida(
                titulo="Banner para Feira",
                descricao="Demanda de Marketing",
                departamento_id="d3",
                categoria_id="c1",
                subcategoria_id="s1",
                setor="TI",
                telefone_contato="11988887777",
                sem_prazo="true",
            ),
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert resp_criar_mkt.status_code == 303
        assert (
            resp_criar_mkt.headers["location"]
            == "/portal/chamados/chamado-mkt-resolvido?avaliar_pendente=1"
        )
        # O chamado para o Marketing não foi inserido
        assert len(repo.criados) == 1


def test_fluxo_usuario_sem_pendencias_abre_qualquer_setor():
    """Usuário sem nenhuma avaliação pendente abre qualquer setor normalmente."""
    repo = FakeRepo(avaliacao_pendente=None)

    with portal_client(repo) as client:
        # Acesso /novo
        resp = client.get("/portal/chamados/novo")
        assert resp.status_code == 200
        assert "(avaliação pendente)" not in resp.text

        # HTMX categorias Marketing
        resp_cat = client.get("/portal/chamados/categorias?departamento_id=d3")
        assert resp_cat.status_code == 200
        assert "HX-Redirect" not in resp_cat.headers


# ==============================================================================
# FLUXO 2: USUÁRIO EM CÓPIA (OBSERVADOR) PODE COMENTAR NO CHAMADO
# ==============================================================================


def test_fluxo_completo_observador_em_copia_comenta():
    """Valida o fluxo completo de interação por usuário em cópia:
    - Chamado pertencente a outro autor ('outro-autor-uuid').
    - Usuário atual (UID) está adicionado em 'observadores'.
    - Usuário atual vê o campo de comentário na tela de detalhe.
    - Usuário atual envia um comentário e este é persistido.
    - E-mail de notificação é agendado para o autor e operador.
    """
    chamado_ativo = _chamado(
        id="chamado-compartilhado",
        cliente_id="outro-autor-uuid",
        operador_id="operador-ti-uuid",
        status="EM_ATENDIMENTO",
        departamento="TI",
    )
    repo = FakeRepo(chamado=chamado_ativo)
    # Coloca o usuário atual (UID) na lista de observadores
    repo._observadores_por_chamado["chamado-compartilhado"] = [
        {"perfil_id": UID, "nome": "Usuário Convidado em Cópia"}
    ]

    with patch("app.notification.agendar_notificacao_email", new_callable=AsyncMock) as mock_notif:
        with portal_client(repo) as client:
            # 1. Usuário em cópia acessa o detalhe do chamado
            resp_detalhe = client.get("/portal/chamados/chamado-compartilhado")
            assert resp_detalhe.status_code == 200
            # Formulário de resposta deve estar presente no HTML
            assert 'action="/portal/chamados/chamado-compartilhado/mensagens"' in resp_detalhe.text
            assert 'placeholder="Escreva uma mensagem…"' in resp_detalhe.text

            # 2. Usuário em cópia envia uma mensagem/comentário
            token = _csrf(client)
            resp_msg = client.post(
                "/portal/chamados/chamado-compartilhado/mensagens",
                data={"conteudo": "Acompanhando por aqui. O erro também ocorreu comigo ontem."},
                headers={"X-CSRF-Token": token},
                follow_redirects=False,
            )
            assert resp_msg.status_code == 303
            assert resp_msg.headers["location"] == "/portal/chamados/chamado-compartilhado"

            # 3. Confere que a mensagem foi gravada no repositório com o ID do usuário em cópia
            assert len(repo.mensagens_criadas) == 1
            msg_gravada = repo.mensagens_criadas[0]
            assert msg_gravada["chamado_id"] == "chamado-compartilhado"
            assert (
                msg_gravada["conteudo"]
                == "Acompanhando por aqui. O erro também ocorreu comigo ontem."
            )

            # 4. Confere que as notificações por e-mail foram disparadas
            mock_notif.assert_called_once()
            args, kwargs = mock_notif.call_args
            # O remetente da notificação é o UID do usuário em cópia
            assert args[2] == UID
            # Observadores informados na lista de notificação
            assert kwargs["observadores"] == [UID]


def test_fluxo_usuario_estranho_nao_pode_comentar():
    """Usuário que NÃO é o autor e NÃO está em cópia não pode comentar."""
    chamado_ativo = _chamado(
        id="chamado-privado",
        cliente_id="autor-uuid",
        operador_id="operador-uuid",
        status="EM_ATENDIMENTO",
        departamento="TI",
    )
    repo = FakeRepo(chamado=chamado_ativo)
    # Ninguém em cópia
    repo._observadores_por_chamado["chamado-privado"] = []

    with portal_client(repo) as client:
        # 1. Tela de detalhe: formulário NÃO deve aparecer
        resp_detalhe = client.get("/portal/chamados/chamado-privado")
        assert resp_detalhe.status_code == 200
        assert 'action="/portal/chamados/chamado-privado/mensagens"' not in resp_detalhe.text

        # 2. Tentativa direta de POST: retorna 403 Forbidden
        token = _csrf(client)
        resp_msg = client.post(
            "/portal/chamados/chamado-privado/mensagens",
            data={"conteudo": "Tentativa indevida de terceiro."},
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert resp_msg.status_code == 403
        assert "Só o autor ou usuários em cópia podem enviar mensagens." in resp_msg.text
        assert len(repo.mensagens_criadas) == 0


def test_fluxo_chamado_resolvido_bloqueia_respostas_para_todos():
    """Quando o chamado está RESOLVIDO, nem o autor nem quem está em cópia comentam."""
    chamado_resolvido = _chamado(
        id="chamado-fechado",
        cliente_id="outro-autor-uuid",
        status="RESOLVIDO",
        departamento="TI",
    )
    repo = FakeRepo(chamado=chamado_resolvido)
    repo._observadores_por_chamado["chamado-fechado"] = [
        {"perfil_id": UID, "nome": "Usuário Convidado"}
    ]

    with portal_client(repo) as client:
        # Formulário não aparece no detalhe
        resp_detalhe = client.get("/portal/chamados/chamado-fechado")
        assert resp_detalhe.status_code == 200
        assert 'action="/portal/chamados/chamado-fechado/mensagens"' not in resp_detalhe.text

        # POST bloqueado
        token = _csrf(client)
        resp_post = client.post(
            "/portal/chamados/chamado-fechado/mensagens",
            data={"conteudo": "Tentando falar no resolvido."},
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
        assert resp_post.status_code == 403
        assert len(repo.mensagens_criadas) == 0

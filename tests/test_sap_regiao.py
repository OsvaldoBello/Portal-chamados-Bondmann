from unittest.mock import MagicMock, patch

import pytest

from app.services.sap_regiao import RegiaoInfo, SAPRegiaoService, obter_regiao_por_email


@pytest.fixture(autouse=True)
def limpar_cache():
    import app.cache as cache
    cache.clear()
    yield
    cache.clear()


def test_regiao_representante_unica():
    service = SAPRegiaoService()
    
    mock_session = MagicMock()
    mock_session.post.return_value.status_code = 200
    mock_session.post.return_value.json.return_value = {"SessionId": "test-session"}

    # Mock BusinessPartners
    bp_resp = MagicMock()
    bp_resp.status_code = 200
    bp_resp.json.return_value = {
        "value": [
            {"CardCode": "F000926", "CardName": "057 - DOUGLAS MELO", "CardType": "cSupplier"}
        ]
    }

    # Mock IB_CO_REGIAO
    reg_resp = MagicMock()
    reg_resp.status_code = 200
    reg_resp.json.return_value = {
        "value": [
            {"Code": "057-VACARIA", "Name": "057-VACARIA", "U_IB_CodCom1": "F000926"}
        ]
    }

    mock_session.get.side_effect = [bp_resp, reg_resp]
    service._session = mock_session

    info = service.consultar_regiao_sap("douglas.melo@bondmann.com.br")
    assert info is not None
    assert info.tipo == "representante"
    assert info.rotulo == "057-VACARIA"
    assert info.regioes == ["057-VACARIA"]
    assert info.eh_multiplo is False


def test_regiao_supervisor_multiplas():
    service = SAPRegiaoService()
    
    mock_session = MagicMock()
    mock_session.post.return_value.status_code = 200
    mock_session.post.return_value.json.return_value = {"SessionId": "test-session"}

    bp_resp = MagicMock()
    bp_resp.status_code = 200
    bp_resp.json.return_value = {
        "value": [
            {"CardCode": "RH2011", "CardName": "ELIEZER JERKE", "CardType": "cSupplier"}
        ]
    }

    reg_resp = MagicMock()
    reg_resp.status_code = 200
    reg_resp.json.return_value = {
        "value": [
            {"Code": "033-POA (ZONA NORTE)", "U_IB_CodCom3": "RH2011"},
            {"Code": "009-BENTO GONCALVES", "U_IB_CodCom3": "RH2011"},
            {"Code": "011-PANAMBI", "U_IB_CodCom3": "RH2011"},
        ]
    }

    mock_session.get.side_effect = [bp_resp, reg_resp]
    service._session = mock_session

    info = service.consultar_regiao_sap("eliezer.jerke@bondmann.com.br")
    assert info is not None
    assert info.tipo == "supervisor"
    assert info.rotulo == "3 regiões vinculadas"
    assert len(info.regioes) == 3
    assert info.eh_multiplo is True
    assert "033-POA (ZONA NORTE)" in info.detalhe


def test_regiao_cache():
    import app.cache as cache
    info_pre = RegiaoInfo(tipo="representante", rotulo="038-SANTA MARIA", regioes=["038-SANTA MARIA"], detalhe="038-SANTA MARIA", eh_multiplo=False)
    cache.set("sap_regiao:allan.toledo@bondmann.com.br", info_pre, ttl=100)

    # Calling obter_regiao_por_email shouldn't call SAP if cache hit
    with patch("app.services.sap_regiao.get_sap_regiao_service") as mock_get_svc:
        res = obter_regiao_por_email("allan.toledo@bondmann.com.br")
        assert res == info_pre
        mock_get_svc.assert_not_called()


def test_regiao_sap_offline():
    service = SAPRegiaoService()
    mock_session = MagicMock()
    mock_session.post.side_effect = Exception("Connection timed out")
    service._session = mock_session

    info = service.consultar_regiao_sap("qualquer@bondmann.com.br")
    assert info is None


@pytest.mark.asyncio
async def test_obter_regiao_do_autor_via_banco():
    from unittest.mock import AsyncMock

    from app.services.sap_regiao import obter_regiao_do_autor

    cliente_id = "1d2f1b28-da53-4606-ba0b-5558f07ad725"
    mock_row_email = "helmano.silva@bondmann.com.br"
    mock_db_regiao = RegiaoInfo(
        tipo="supervisor",
        rotulo="6 regiões vinculadas",
        regioes=["136-VALE DO ACO", "069-DIVINOPOLIS"],
        detalhe="136-VALE DO ACO, 069-DIVINOPOLIS",
        eh_multiplo=True,
    )

    with patch("app.services.sap_regiao.obter_email_do_autor", new_callable=AsyncMock) as mock_email:
        mock_email.return_value = mock_row_email
        with patch("app.services.sap_regiao.obter_regiao_do_banco", new_callable=AsyncMock) as mock_banco:
            mock_banco.return_value = mock_db_regiao
            info = await obter_regiao_do_autor(cliente_id)
            assert info is not None
            assert info.tipo == "supervisor"
            assert info.rotulo == "6 regiões vinculadas"
            assert info.eh_multiplo is True
            mock_banco.assert_awaited_once_with(mock_row_email)


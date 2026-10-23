from app.config import Settings


def test_sap_settings_defaults(monkeypatch):
    monkeypatch.setenv("SAP_API_URL", "https://10.151.4.40:50000/b1s/v1")
    monkeypatch.setenv("SAP_COMPANY_DB", "SBO_BONDMANN_PRD")
    monkeypatch.setenv("SAP_USERNAME", "ADMTI")
    monkeypatch.setenv("SAP_PASSWORD", "calcio20")
    monkeypatch.setenv("SAP_PROXY_URL", "")

    s = Settings()
    assert s.sap_api_url == "https://10.151.4.40:50000/b1s/v1"
    assert s.sap_company_db == "SBO_BONDMANN_PRD"
    assert s.sap_username == "ADMTI"
    assert s.sap_password == "calcio20"
    assert s.sap_proxy_url == ""

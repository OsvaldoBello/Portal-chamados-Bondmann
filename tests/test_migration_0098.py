from pathlib import Path


def test_migration_0098_syntax_and_structure():
    migration_path = Path("supabase/migrations/0098_sap_regioes_autores.sql")
    assert migration_path.exists()
    content = migration_path.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS public.sap_regioes_autores" in content
    assert "ENABLE ROW LEVEL SECURITY" in content
    assert "sap_regioes_autores_select" in content
    assert "sap_regioes_autores_admin_write" in content

from pathlib import Path
import importlib

from fastapi.testclient import TestClient

from app import app, _format_date_string


def test_get_supabase_reuses_single_client(monkeypatch):
    import database.supabase_client as supabase_client

    calls = []

    class DummyClient:
        pass

    def fake_create_client(url, key):
        calls.append((url, key))
        return DummyClient()

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")
    monkeypatch.setattr(supabase_client, "_SUPABASE_CLIENT", None, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "supabase", type("S", (), {"create_client": staticmethod(fake_create_client)}))

    first = supabase_client.get_supabase()
    second = supabase_client.get_supabase()

    assert first is second
    assert len(calls) == 1


def test_liq_views_use_correct_business_rules():
    js = Path("static/script.js").read_text(encoding="utf-8", errors="ignore")
    # Procesar: notificación enviada, pago ingresado, pero no procesando
    assert "notificacion === 'enviado' && pago === 'ingresado' && estado_ctl !== 'procesando'" in js
    # Pendientes: registros con notificación enviada y aún no ingresó el pago
    assert "notificacion === 'enviado' && pago !== 'ingresado'" in js


def test_liq_stats_ui_reads_backend_counts():
    js = Path("static/script.js").read_text(encoding="utf-8", errors="ignore")
    assert "stats.table_counts || stats" in js
    assert "TABLE_COUNTS.liq_2025" in js
    assert "TABLE_COUNTS.liq_2026" in js


def test_pendientes_filter_matches_business_rule():
    js = Path("static/script.js").read_text(encoding="utf-8", errors="ignore")
    assert "notificacion === 'enviado' && pago !== 'ingresado'" in js
    assert "estado_ctl !== 'enviado' && estado_ctl !== 'procesando'" in js


def test_move_liq_to_table_normalizes_dates_before_insert(monkeypatch):
    import database.supabase_client as supabase_client

    captured = {}

    class DummyTable:
        def __init__(self, name):
            self.name = name
            self.inserted = None

        def select(self, *args, **kwargs):
            return self

        def eq(self, *args, **kwargs):
            return self

        def execute(self):
            if self.name == 'liq':
                return type('Resp', (), {'data': [{'escritura': '123', 'fecha_proceso': '30/08/26'}]})()
            if self.name == 'liq_2026':
                captured['record'] = self.inserted
                return type('Resp', (), {'data': []})()
            return type('Resp', (), {'data': []})()

        def insert(self, row):
            self.inserted = row
            return self

        def delete(self):
            return self

    class DummyDB:
        def table(self, name):
            return DummyTable(name)

    monkeypatch.setattr(supabase_client, 'get_supabase', lambda: DummyDB())
    result = supabase_client.move_liq_to_table('123', 'liq_2026')

    assert result['ok'] is True
    assert captured['record']['fecha_proceso'] == '2026-08-30'


def test_root_page_serves_html():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_format_date_string_returns_postgres_iso_date():
    assert _format_date_string("13/08/26") == "2026-08-13"
    assert _format_date_string("13/08/2026") == "2026-08-13"
    assert _format_date_string("2026-08-13") == "2026-08-13"


def test_sync_liq_from_actas_updates_payment_to_procesar_only(monkeypatch):
    import database.supabase_client as supabase_client

    captured = {}

    class DummyTable:
        def __init__(self, name):
            self.name = name
        def update(self, payload):
            captured['payload'] = payload
            return self
        def eq(self, *args, **kwargs):
            return self
        def execute(self):
            return type('Resp', (), {'data': [{'ok': True}]})()

    class DummyDB:
        def table(self, name):
            return DummyTable(name)

    monkeypatch.setattr(supabase_client, 'get_supabase', lambda: DummyDB())
    monkeypatch.setattr(supabase_client, '_table_exists', lambda db, name: name == 'liq')

    result = supabase_client.sync_liq_estado_from_actas('123', True)
    assert result['pago'] == 'procesar'
    assert 'estado_ctl' not in result
    assert captured['payload'] == {'pago': 'procesar'}


def test_get_gobernaciones_returns_default_values_when_table_missing(monkeypatch):
    import database.supabase_client as supabase_client

    monkeypatch.setattr(supabase_client, 'get_supabase', lambda: None)
    assert supabase_client.get_gobernaciones()
    assert 'cundinamarca' in [g.lower() for g in supabase_client.get_gobernaciones()]


def test_workflow_endpoints_trigger_expected_methods(monkeypatch):
    calls = []

    class DummyWorkflowService:
        def __init__(self, *args, **kwargs):
            pass

        def ejecutar_certificados(self):
            calls.append("ejecutar_certificados")

        def ejecutar_recibos(self):
            calls.append("ejecutar_recibos")

        def ejecutar_envio_certificados(self):
            calls.append("ejecutar_envio_certificados")

    monkeypatch.setattr("app.WorkflowService", DummyWorkflowService)

    client = TestClient(app)

    response_certificados = client.post("/api/descargas/certificados/start")
    response_recibos = client.post("/api/envios/recibos/start")
    response_envio_certificados = client.post("/api/envios/certificados/start")

    assert response_certificados.status_code == 200
    assert response_recibos.status_code == 200
    assert response_envio_certificados.status_code == 200
    assert calls == [
        "ejecutar_certificados",
        "ejecutar_recibos",
        "ejecutar_envio_certificados",
    ]

from pathlib import Path

from fastapi.testclient import TestClient

from app import app, _format_date_string


def test_liq_views_use_correct_business_rules():
    html = Path("static/notaria_app.html").read_text(encoding="utf-8", errors="ignore")
    # Procesar: notificación enviada, pago ingresado, pero no procesando
    assert "notificacion === 'enviado' && pago === 'ingresado' && estado_ctl !== 'procesando'" in html
    # Pendientes: registros con notificación enviada
    assert "return notificacion === 'enviado'" in html


def test_liq_stats_ui_reads_backend_counts():
    html = Path("static/notaria_app.html").read_text(encoding="utf-8", errors="ignore")
    assert "stats.table_counts || stats" in html
    assert "TABLE_COUNTS.liq_2025" in html
    assert "TABLE_COUNTS.liq_2026" in html


def test_root_page_serves_html():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_format_date_string_returns_postgres_iso_date():
    assert _format_date_string("13/08/26") == "2026-08-13"
    assert _format_date_string("13/08/2026") == "2026-08-13"
    assert _format_date_string("2026-08-13") == "2026-08-13"


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

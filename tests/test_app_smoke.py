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
    script = Path("static/script.js").read_text(encoding="utf-8", errors="ignore")
    # Procesar: notificación enviada, pago ingresado y CTL pendiente, incluida Ventanilla.
    assert "normalizar(r.estado_ctl) === 'pendiente'" in script
    assert "TABLE_CACHE.liq_2025" in script
    assert "normalizar(r.pago) === 'ingresado'" in script
    # Pendientes: registros con notificación enviada
    assert "return notificacion === 'enviado'" in script


def test_liq_stats_ui_reads_backend_counts():
    script = Path("static/script.js").read_text(encoding="utf-8", errors="ignore")
    assert "stats.table_counts || stats" in script
    assert "TABLE_COUNTS.liq_2025" in script
    assert "TABLE_COUNTS.liq_2026" in script


def test_root_page_serves_html():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_format_date_string_returns_postgres_iso_date():
    assert _format_date_string("13/08/26") == "2026-08-13"
    assert _format_date_string("13/08/2026") == "2026-08-13"
    assert _format_date_string("2026-08-13") == "2026-08-13"


def test_pagos2026_report_filters_and_groups_by_governorship(monkeypatch):
    data = {
        "actas": [
            {"escritura": 101, "acta": "A-1", "valor_acta": 100},
            {"escritura": 102, "acta": "B-1", "valor_acta": 200},
        ],
        "pagos_2026": [
            {"escritura": 101, "fecha_pago": "2026-09-30", "vr_ben": 60, "vr_reg": 40},
            {"escritura": 102, "fecha_pago": "2026-09-30", "vr_ben": 120, "vr_reg": 80},
            {"escritura": 103, "fecha_pago": "2026-10-01", "vr_ben": 1, "vr_reg": 1},
        ],
        "liq": [
            {"escritura": 101, "gobernacion": "antioquia", "nir": "N-101"},
            {"escritura": 102, "gobernacion": "cundinamarca", "nir": "N-102"},
            {"escritura": 103, "gobernacion": "antioquia", "nir": "N-103"},
        ],
    }

    class Query:
        def __init__(self, table):
            self.table_name = table
            self.filters = {}

        def select(self, *_args):
            return self

        def order(self, *_args, **_kwargs):
            return self

        def eq(self, field, value):
            self.filters[field] = value
            return self

        def execute(self):
            rows = data[self.table_name]
            for field, value in self.filters.items():
                rows = [row for row in rows if row.get(field) == value]
            return type("Response", (), {"data": rows})()

    class DummySupabase:
        def table(self, name):
            return Query(name)

    monkeypatch.setattr("app.get_supabase", lambda: DummySupabase())
    client = TestClient(app)

    grouped = client.get("/api/pagos2026/informe?fecha_pago=2026-09-30&gobernacion=todas")
    assert grouped.status_code == 200
    assert "Gobernación: Antioquia" in grouped.text
    assert "Gobernación: Cundinamarca" in grouped.text
    assert "2026-10-01" not in grouped.text

    single = client.get("/api/pagos2026/informe?fecha_pago=2026-09-30&gobernacion=antioquia")
    assert single.status_code == 200
    assert "Gobernación: Antioquia" in single.text
    assert "Gobernación: Cundinamarca" not in single.text
    assert "102" not in single.text


def test_unified_payment_detail_returns_payment_and_actas(monkeypatch):
    class Query:
        def select(self, *_args):
            return self

        def eq(self, *_args):
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            return type("Response", (), {"data": [{"escritura": 456, "vr_ben": 1200, "liquidacion": "L-1"}]})()

    class DummySupabase:
        def table(self, _name):
            return Query()

    monkeypatch.setattr("app.get_supabase", lambda: DummySupabase())
    monkeypatch.setattr("app.get_actas_by_escritura", lambda escritura: [{"escritura": escritura, "acta": "A-1"}])

    response = TestClient(app).get("/api/pagos2026/456/detalle")

    assert response.status_code == 200
    assert response.json() == {
        "pago": {"escritura": 456, "vr_ben": 1200, "liquidacion": "L-1"},
        "actas": [{"escritura": 456, "acta": "A-1"}],
    }


def test_register_catalog_value_is_persistent(monkeypatch):
    rows = []

    class Query:
        def __init__(self):
            self.payload = None

        def upsert(self, payload, **_kwargs):
            self.payload = payload
            return self

        def select(self, *_args):
            return self

        def order(self, *_args):
            return self

        def execute(self):
            if self.payload:
                rows[:] = [row for row in rows if (row["tipo"], row["clave"]) != (self.payload["tipo"], self.payload["clave"])]
                rows.append(self.payload)
            return type("Response", (), {"data": rows})()

    class DummySupabase:
        def table(self, _name):
            return Query()

    monkeypatch.setattr("app.get_supabase", lambda: DummySupabase())
    client = TestClient(app)

    saved = client.post("/api/catalogos-registro", json={"tipo": "gobernacion", "valor": "quindio"})
    catalogos = client.get("/api/catalogos-registro")

    assert saved.status_code == 200
    assert "quindio" in catalogos.json()["gobernacion"]


def test_payment_patch_keeps_explicit_null_values(monkeypatch):
    calls = []
    monkeypatch.setattr("app.get_supabase", lambda: object())

    def fake_upsert(escritura, **changes):
        calls.append((escritura, changes))
        return {"data": []}

    monkeypatch.setattr("app.upsert_pago_2026", fake_upsert)

    response = TestClient(app).patch("/api/pagos2026/456", json={"fecha_pago": None})

    assert response.status_code == 200
    assert calls == [(456, {"fecha_pago": None})]


def test_actas_marked_retired_when_payment_is_entered(monkeypatch):
    calls = []
    monkeypatch.setattr("app.get_supabase", lambda: object())
    monkeypatch.setattr("app.update_liq_row", lambda escritura, body: type("Result", (), {"data": [{"escritura": escritura, **body}]})())
    monkeypatch.setattr("app.retirar_actas_por_escritura", lambda escritura: calls.append(escritura))
    client = TestClient(app)

    entered = client.patch("/api/liq/456", json={"pago": "ingresado"})
    pending = client.patch("/api/liq/789", json={"pago": "pendiente"})

    assert entered.status_code == 200
    assert pending.status_code == 200
    assert calls == ["456"]


def test_retired_actas_are_not_active():
    from database.supabase_client import acta_esta_activa

    assert acta_esta_activa({"estado": None})
    assert acta_esta_activa({"estado": "Activa"})
    assert not acta_esta_activa({"estado": "Retirada"})
    assert not acta_esta_activa({"estado": "Retiradas"})
    assert not acta_esta_activa({"estado": "Anulada"})


def test_payment_summary_ignores_retired_actas(monkeypatch):
    import database.supabase_client as supabase_client

    table_data = {
        "actas": [
            {"escritura": 101, "tipo_deposito": "Beneficencia", "valor_acta": 50, "estado": "activa", "anio": 2026},
            {"escritura": 102, "tipo_deposito": "Beneficencia", "valor_acta": 90, "estado": "retirada", "anio": 2026},
        ],
        "pagos_2026": [
            {"escritura": 101, "vr_ben": 50, "vr_reg": 0},
            {"escritura": 102, "vr_ben": 90, "vr_reg": 0},
        ],
    }

    class Query:
        def __init__(self, table):
            self.table = table
            self.filters = {}

        def select(self, *_args):
            return self

        def eq(self, field, value):
            self.filters[field] = value
            return self

        def execute(self):
            data = table_data[self.table]
            for field, value in self.filters.items():
                data = [row for row in data if row.get(field) == value]
            return type("Response", (), {"data": data})()

    class DummySupabase:
        def table(self, name):
            return Query(name)

    monkeypatch.setattr(supabase_client, "get_supabase", lambda: DummySupabase())

    summary = supabase_client.get_pagos_2026_resumen()
    active_writings = supabase_client.get_escrituras_con_actas_activas()

    assert [row["escritura"] for row in summary] == [101]
    assert summary[0]["pago_cli"] == 50
    assert active_writings == ["101"]


def test_certificate_queue_includes_liq_and_ventanilla_pending_rows(monkeypatch):
    from repositories.liq_repository import LiqRepository

    calls = []
    rows_by_table = {
        "liq": [{"escritura": 101, "estado_ctl": "pendiente"}],
        "liq_2025": [{"escritura": 202, "estado_ctl": "pendiente"}],
    }

    class Query:
        def __init__(self, table):
            self.table = table
            self.filters = {}

        def select(self, *_args):
            return self

        def eq(self, field, value):
            self.filters[field] = value
            return self

        def execute(self):
            calls.append((self.table, self.filters.copy()))
            return type("Response", (), {"data": rows_by_table[self.table]})()

    class DummySupabase:
        def table(self, name):
            return Query(name)

    repo = LiqRepository()
    repo.supabase = DummySupabase()

    pending = repo.certificados_pendientes()

    assert [(row["escritura"], row["tabla_origen"]) for row in pending] == [(101, "liq"), (202, "liq_2025")]
    assert [table for table, _filters in calls] == ["liq", "liq_2025"]
    assert all(filters == {"pago": "ingresado", "notificacion": "enviado", "estado_ctl": "pendiente"} for _table, filters in calls)


def test_ventanilla_patch_updates_only_ventanilla(monkeypatch):
    calls = []
    monkeypatch.setattr("app.get_supabase", lambda: object())
    monkeypatch.setattr(
        "app.update_liq_row_in_table",
        lambda table, escritura, body: calls.append((table, escritura, body)) or {"data": []},
    )
    monkeypatch.setattr("app.retirar_actas_por_escritura", lambda escritura: calls.append(("retirar", escritura)))

    response = TestClient(app).patch(
        "/api/liq/table/liq_2025/202",
        json={"pago": "ingresado", "notificacion": "enviado", "estado_ctl": "pendiente"},
    )

    assert response.status_code == 200
    assert calls == [("liq_2025", "202", {"pago": "ingresado", "notificacion": "enviado", "estado_ctl": "pendiente"})]


def test_processing_certificate_updates_origin_table():
    from repositories.liq_repository import LiqRepository

    calls = []

    class Query:
        def __init__(self, table):
            self.table = table

        def update(self, body):
            self.body = body
            return self

        def eq(self, field, value):
            calls.append((self.table, field, value, self.body))
            return self

        def execute(self):
            return type("Response", (), {"data": [{"ok": True}]})()

    class DummySupabase:
        def table(self, name):
            return Query(name)

    repo = LiqRepository()
    repo.supabase = DummySupabase()
    repo.actualizar_estado_ctl(202, "enviado", "liq_2025")

    assert calls == [("liq_2025", "escritura", 202, {"estado_ctl": "enviado"})]


def test_ventanilla_combines_tables_without_active_actas(monkeypatch):
    monkeypatch.setattr("app.get_supabase", lambda: object())
    monkeypatch.setattr("app.get_escrituras_con_actas_activas", lambda: ["300"])
    data = {
        "liq_2025": [{"escritura": 200, "responsable": "Ventanilla vieja"}],
        "liq": [
            {"escritura": 200, "responsable": "Duplicado reciente"},
            {"escritura": 300, "responsable": "Con acta"},
            {"escritura": 400, "responsable": "Sin acta"},
        ],
    }

    def fake_get_rows(table_name, **_kwargs):
        return type("Response", (), {"data": data[table_name]})()

    monkeypatch.setattr("app.get_table_rows", fake_get_rows)
    response = TestClient(app).get("/api/ventanilla")

    assert response.status_code == 200
    assert [(row["escritura"], row["tabla_origen"]) for row in response.json()] == [
        (200, "liq_2025"),
        (400, "liq"),
    ]


def test_sheet_report_options_and_filtering(monkeypatch):
    rows = [
        {"escritura": 1, "responsable": "Ana", "gobernacion": "Tolima", "estado_ctl": "pendiente"},
        {"escritura": 2, "responsable": "Luis", "gobernacion": "Antioquia", "estado_ctl": "enviado"},
    ]
    monkeypatch.setattr("app._filas_para_informe", lambda hoja: rows)
    client = TestClient(app)

    options = client.get("/api/informes/opciones?hoja=registros")
    report = client.get("/api/informes/tabla?hoja=registros&responsable=Ana&gobernacion=Tolima&estado=pendiente")

    assert options.status_code == 200
    assert options.json() == {
        "responsables": ["Ana", "Luis"],
        "gobernaciones": ["Antioquia", "Tolima"],
        "estados": ["enviado", "pendiente"],
    }
    assert report.status_code == 200
    assert "Escritura 1" not in report.text
    assert "<td>1</td>" in report.text
    assert "<td>2</td>" not in report.text


def test_liq_import_maps_benef_and_payment_fields(monkeypatch):
    captured = {}
    monkeypatch.setattr("app.get_supabase", lambda: object())
    monkeypatch.setattr("app.insert_log", lambda *args, **kwargs: None)

    def fake_import(rows, batch_size, payment_rows):
        captured["rows"] = rows
        captured["payments"] = payment_rows
        return {"nuevos": 1, "actualizados": 0}

    monkeypatch.setattr("app.import_liq_from_rows", fake_import)
    csv_data = (
        "Escritura;Nir;Responsable;Gobernacion;Correo;Notificacion;Cert;Pago;Copias;Fecha_liq;Benef;"
        "Fecha_Pago;Radicado;Ingreso;Estado_Ctl;Observaciones;Vr_Ben;Vr_Reg;Liquidacion\n"
        "7788;NIR-7788;Ana;Tolima;ana@example.com;enviado;cert-1;ingresado;2;01-09-26;15-10-26;"
        "20-10-26;RAD-7788;OK;pendiente;Obs;1.250.000;340,50;LIQ-55\n"
    ).encode("utf-8")

    response = TestClient(app).post(
        "/api/liq/import",
        files={"file": ("liquidaciones.csv", csv_data, "text/csv")},
    )

    assert response.status_code == 200, response.text
    assert captured["rows"][0]["benef"] == "2026-10-15"
    assert captured["rows"][0]["estado_ctl"] == "pendiente"
    assert captured["payments"] == [{
        "escritura": 7788,
        "vr_ben": 1250000,
        "vr_reg": 340.5,
        "liquidacion": "LIQ-55",
        "fecha_pago": "2026-10-15",
    }]

def test_liq_xlsx_import_keeps_actas_separate_and_maps_benef_date(monkeypatch):
    from datetime import datetime
    from io import BytesIO

    from openpyxl import Workbook

    captured = {}
    monkeypatch.setattr("app.get_supabase", lambda: object())
    monkeypatch.setattr("app.insert_log", lambda *args, **kwargs: None)

    def fake_import(rows, batch_size, payment_rows):
        captured["rows"] = rows
        captured["payments"] = payment_rows
        return {"nuevos": 1, "actualizados": 0}

    monkeypatch.setattr("app.import_liq_from_rows", fake_import)

    workbook = Workbook()
    workbook.active.title = "Gobernaciones"
    workbook.active.append(["DEPARTAMENTO", "LINK"])
    workbook.active.append(["No importar", ""])
    worksheet = workbook.create_sheet("Liq.")
    worksheet.append(["Informe Not 38"])
    worksheet.append(["Escritura", "Notificado"])
    worksheet.append(["7037", ""])
    worksheet.append([
        "Escritura", "Cert", "Nir", "Responsable", "Gobernacion", "Liquidacion",
        "Vr_Ben", "Vr_Reg", "Estado_Acta", "Benef", "Ingreso", "Actas",
        "Observaciones", "Notificacion", "Estado_Ctl", "correo", "Copias",
        "Fecha_liq", "Fecha_Pago", "Radicado",
    ])
    worksheet.append([
        "7788", "CERT-1", "NIR-7788", "Ana", "Tolima", "LIQ-55",
        1250000, 340500, -1250000, datetime(2026, 10, 15), "Si", "55-56",
        "Observación", "Enviado", "Procesando", "ana@example.com", 2,
        datetime(2026, 9, 1), datetime(2026, 10, 20), "RAD-7788",
    ])
    file_bytes = BytesIO()
    workbook.save(file_bytes)

    response = TestClient(app).post(
        "/api/liq/import",
        files={"file": ("Informe Not 38.xlsx", file_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 200, response.text
    assert captured["rows"][0]["benef"] == "2026-10-15"
    assert captured["rows"][0]["fecha_pago"] == "2026-10-20"
    assert captured["rows"][0]["estado_ctl"] == "Procesando"
    assert captured["payments"] == [{
        "escritura": 7788,
        "vr_ben": 1250000,
        "vr_reg": 340500,
        "liquidacion": "LIQ-55",
        "fecha_pago": "2026-10-15",
    }]


def test_catalogs_fall_back_to_persistent_local_file_when_supabase_table_is_missing(monkeypatch, tmp_path):
    catalog_file = tmp_path / "catalogos_registro.json"

    class MissingCatalogTable:
        def table(self, _table):
            raise RuntimeError("catalogos_registro table is missing")

    monkeypatch.setattr("app.CATALOGOS_REGISTRO_LOCAL_FILE", catalog_file)
    monkeypatch.setattr("app.get_supabase", lambda: MissingCatalogTable())
    client = TestClient(app)

    saved = client.post(
        "/api/catalogos-registro",
        json={"tipo": "estado_ctl", "valor": "esperando revisión"},
    )
    catalogos = client.get("/api/catalogos-registro")

    assert saved.status_code == 200
    assert saved.json()["storage"] == "local"
    assert "esperando revisión" in catalogos.json()["estado_ctl"]
    assert catalog_file.exists()


def test_import_liq_upserts_associated_payments(monkeypatch):
    import database.supabase_client as supabase_client

    calls = []

    class Query:
        def __init__(self, table):
            self.table = table
            self.payload = None

        def select(self, *_args):
            return self

        def in_(self, *_args):
            return self

        def execute(self):
            return type("Response", (), {"data": []})()

        def insert(self, payload):
            self.payload = payload
            calls.append((self.table, "insert", payload))
            return self

        def update(self, payload):
            self.payload = payload
            calls.append((self.table, "update", payload))
            return self

        def eq(self, *_args):
            return self

        def upsert(self, payload, **kwargs):
            calls.append((self.table, "upsert", payload, kwargs))
            return self

    class DummySupabase:
        def table(self, table_name):
            return Query(table_name)

    monkeypatch.setattr(supabase_client, "get_supabase", lambda: DummySupabase())
    result = supabase_client.import_liq_from_rows(
        [{"escritura": 7788, "benef": "2026-10-15", "cert": "cert-1"}],
        payment_rows=[{"escritura": 7788, "vr_ben": 1250000, "vr_reg": 300, "liquidacion": "LIQ-55", "fecha_pago": "2026-10-15"}],
    )

    assert result["pagos_actualizados"] == 1
    assert any(call[0] == "liq" and call[1] == "insert" for call in calls)
    assert any(call[0] == "pagos_2026" and call[1] == "upsert" for call in calls)
    payment_upsert = next(call for call in calls if call[0] == "pagos_2026" and call[1] == "upsert")
    assert payment_upsert[2][0]["vr_ben"] == 1250000
    assert payment_upsert[2][0]["vr_reg"] == 300


def test_import_liq_preserves_existing_payment_amount_when_excel_cell_is_blank(monkeypatch):
    import database.supabase_client as supabase_client

    calls = []

    class Query:
        def __init__(self, table):
            self.table = table

        def select(self, *_args):
            return self

        def in_(self, _field, _values):
            return self

        def execute(self):
            rows = [{
                "escritura": 7788,
                "vr_ben": 1250000,
                "vr_reg": 340500,
                "liquidacion": "LIQ-OLD",
                "fecha_pago": "2026-10-15",
            }] if self.table == "pagos_2026" else []
            return type("Response", (), {"data": rows})()

        def insert(self, _payload):
            return self

        def upsert(self, payload, **_kwargs):
            calls.append((self.table, payload))
            return self

    class DummySupabase:
        def table(self, table_name):
            return Query(table_name)

    monkeypatch.setattr(supabase_client, "get_supabase", lambda: DummySupabase())
    supabase_client.import_liq_from_rows(
        [{"escritura": 7788}],
        payment_rows=[{"escritura": 7788, "vr_reg": 400000}],
    )

    payment_upsert = next(payload for table, payload in calls if table == "pagos_2026")
    assert payment_upsert == [{
        "escritura": 7788,
        "vr_ben": 1250000,
        "vr_reg": 400000,
        "liquidacion": "LIQ-OLD",
        "fecha_pago": "2026-10-15",
    }]


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

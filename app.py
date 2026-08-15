import base64
from datetime import datetime
from email.message import EmailMessage
import io
import logging
import os
from pathlib import Path
import re
import smtplib
import subprocess
import sys
import threading
from typing import Any, Optional
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import pandas as pd
import uvicorn

# Cargar variables de entorno
load_dotenv()

# Importaciones de módulos locales
try:
    from database.supabase_client import (
        check_table_exists,
        delete_acta,
        export_liq_to_excel,
        get_acta_by_id,
        get_actas_by_escritura,
        get_all_liq,
        get_liq_stats,
        get_pagos_2026_resumen,
        get_pending_liq,
        get_processed_liq,
        get_supabase,
        get_table_rows,
        guardar_descarga,
        import_actas_from_rows,
        import_liq_from_rows,
        import_pagos_consolidado_from_rows,
        import_pagos_from_rows,
        insert_acta,
        insert_liq_row,
        insert_log,
        marcar_descarga_como_enviada,
        move_liq_to_table,
        obtener_descargas_pendientes_de_envio,
        obtener_descargas_por_escritura,
        update_acta,
        update_liq_estado_by_escritura,
        update_liq_row,
        upsert_pago_2026,
    )
except ImportError:
    from database.supabase_client import (
        check_table_exists,
        delete_acta,
        get_acta_by_id,
        get_actas_by_escritura,
        get_supabase,
        guardar_descarga,
        import_actas_from_rows,
        import_liq_from_rows,
        import_pagos_consolidado_from_rows,
        import_pagos_from_rows,
        insert_log,
        marcar_descarga_como_enviada,
        obtener_descargas_pendientes_de_envio,
        obtener_descargas_por_escritura,
        update_acta,
    )

from services.workflow_service import WorkflowService

# Silenciar las peticiones HTTP del logger de acceso de uvicorn
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
SQL_SCHEMA_FILE = Path(APP_DIR, "supabase_schema_from_excel.sql")

app = FastAPI(title="Sistema Notarial")

# Servir estáticos
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

JOBS = {}  # job_id -> {"status": "running|done|error", "logs": [str], "returncode": int|None}

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def _append(job_id: str, line: str):
    if job_id in JOBS:
        JOBS[job_id]["logs"].append(line.rstrip())

def load_schema_sql() -> str:
    if not SQL_SCHEMA_FILE.exists():
        raise RuntimeError(f"No se encontró el archivo de esquema: {SQL_SCHEMA_FILE}")
    return SQL_SCHEMA_FILE.read_text(encoding="utf-8")

def _apply_schema_sql(db_url: str, sql: str) -> None:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Instala psycopg con `pip install psycopg[binary]` para ejecutar DDL directamente."
        ) from exc

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

def _get_schema_validation() -> dict:
    required_tables = ["liq", "pagos_2026", "pagos_consolidado"]
    missing = []
    errors = []
    for table in required_tables:
        try:
            exists = check_table_exists(table)
        except Exception as e:
            errors.append({"table": table, "error": str(e)})
            exists = False
        if not exists:
            missing.append(table)
    return {
        "required_tables": required_tables,
        "missing_tables": missing,
        "errors": errors,
        "ok": len(missing) == 0 and len(errors) == 0,
    }

def normalize_import_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = (
        df.columns.astype(str)
        .str.strip()
        .str.lower()
        .str.replace("á", "a")
        .str.replace("é", "e")
        .str.replace("í", "i")
        .str.replace("ó", "o")
        .str.replace("ú", "u")
        .str.replace("ñ", "n")
        .str.replace("\n", " ")
        .str.replace("\r", "")
        .str.replace(" ", "_")
        .str.replace("-", "_")
    )
    return df

def normalizar_escritura(esc):
    if esc is None:
        return ""
    val = re.sub(r'\D', '', str(esc))
    return val.lstrip('0') if val else ""

def _format_date_string(value: Any) -> Any:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None

    m = re.match(r'^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})$', s)
    if m:
        day, month, year = m.groups()
        year = year if len(year) == 4 else f"20{year}"
        try:
            return datetime(int(year), int(month), int(day)).date().isoformat()
        except ValueError:
            pass

    try:
        dt = datetime.fromisoformat(s.replace('Z', '+00:00'))
        return dt.date().isoformat()
    except Exception:
        pass

    try:
        return datetime.strptime(s, '%d/%m/%y').date().isoformat()
    except Exception:
        pass

    return s

def _normalize_date_fields(body: dict) -> dict:
    for field in ['fecha_proceso', 'fecha_pago', 'fecha_acta']:
        if field in body and body[field] is not None:
            body[field] = _format_date_string(body[field])
    return body

# ==========================================
# WORKFLOW JOBS RUNNERS
# ==========================================

def _run_certificados_job(job_id: str):
    try:
        JOBS[job_id]["status"] = "running"
        workflow = WorkflowService()
        workflow.ejecutar_certificados()
        JOBS[job_id]["returncode"] = 0
        JOBS[job_id]["status"] = "done"
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        _append(job_id, f"[EXCEPTION] {type(e).__name__}: {e}")

def _run_workflow_job(job_id: str, method_name: str, body: dict = None):
    try:
        JOBS[job_id]["status"] = "running"
        workflow = WorkflowService()
        method = getattr(workflow, method_name)
        if body is not None:
            method(body)
        else:
            method()
        JOBS[job_id]["returncode"] = 0
        JOBS[job_id]["status"] = "done"
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        _append(job_id, f"[EXCEPTION] {type(e).__name__}: {e}")

# ==========================================
# RUTAS BÁSICAS & VISTAS
# ==========================================

@app.get("/", response_class=HTMLResponse)
def home():
    html_path = os.path.join(STATIC_DIR, "notaria_app.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health():
    return {"status": "ok", "service": "notaria"}

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    favicon_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204)

# ==========================================
# JOBS & WORKFLOWS ENDPOINTS
# ==========================================

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id no existe")
    return job

@app.post("/api/descargas/certificados/start")
def start_descarga_certificados():
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}
    t = threading.Thread(target=_run_certificados_job, args=(job_id,), daemon=True)
    t.start()
    try:
        insert_log("descarga_certificados", f"Job iniciado: {job_id}", "sistema")
    except Exception:
        pass
    return {"job_id": job_id}

@app.post("/api/envios/certificados/start")
def start_envio_certificados():
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}
    t = threading.Thread(target=_run_workflow_job, args=(job_id, 'ejecutar_envio_certificados'), daemon=True)
    t.start()
    return {"job_id": job_id}

@app.post("/api/envios/certificados/unico/start")
def start_envio_certificado_unico(body: dict):
    if not isinstance(body, dict) or not body.get("escritura") or not body.get("correo"):
        raise HTTPException(status_code=400, detail="Escritura y correo son obligatorios")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}
    t = threading.Thread(target=_run_workflow_job, args=(job_id, 'ejecutar_envio_certificado', body), daemon=True)
    t.start()
    return {"job_id": job_id}

@app.post("/api/envios/recibos/start")
def start_envio_recibos():
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}
    t = threading.Thread(target=_run_workflow_job, args=(job_id, 'ejecutar_recibos'), daemon=True)
    t.start()
    return {"job_id": job_id}

# ==========================================
# DASHBOARD & LIQUIDACIONES (LIQ)
# ==========================================

@app.get("/api/dashboard/status")
def dashboard_status():
    try:
        stats = get_liq_stats()
    except Exception:
        stats = {"total": 0, "liq": 0, "liq_2025": 0, "liq_2026": 0}
        
    active_jobs = sum(1 for j in JOBS.values() if j.get("status") in ["running", "queued"])

    return {
        "status": "online",
        "database_connected": get_supabase() is not None,
        "active_background_jobs": active_jobs,
        "stats": stats
    }

@app.get("/api/liq/pending")
def listar_pending(limit: int = 10000, page: int = 1, sort_by: str = 'escritura', desc: bool = False):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        res = get_pending_liq(limit=limit, page=page, sort_by=sort_by, desc=desc)
        return res.data if hasattr(res, "data") else res
    except Exception as e:
        insert_log("consulta_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/liq/stats")
def liq_stats():
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        return get_liq_stats()
    except Exception as e:
        try:
            insert_log("stats_liq", str(e), "sistema", "error")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/liq/all")
def liq_all(limit: int = 1000, page: int = 1, sort_by: str = 'updated_at', desc: bool = True):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        max_limit = min(max(limit, 1), 10000)
        res = get_all_liq(limit=max_limit, page=max(page, 1), sort_by=sort_by, desc=desc)
        return res.data if hasattr(res, "data") else res
    except Exception as e:
        insert_log("consulta_liq_all", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/liq/processed")
def liq_processed(limit: int = 10000, page: int = 1, sort_by: str = 'fecha_proceso', desc: bool = True):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        res = get_processed_liq(limit=limit, page=page, sort_by=sort_by, desc=desc)
        return res.data if hasattr(res, "data") else res
    except Exception as e:
        insert_log("consulta_liq_processed", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/liq/table/{table_name}')
def liq_table(table_name: str, limit: int = 1000, page: int = 1, sort_by: str = None, desc: bool = True):
    try:
        allowed = {'liq', 'liq_2025', 'liq_2026', 'pagos_2026', 'pagos_consolidado'}
        if table_name not in allowed:
            raise HTTPException(status_code=400, detail=f"Tabla no permitida: {table_name}")
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        res = get_table_rows(table_name, limit=limit, page=page, sort_by=sort_by, desc=desc)
        return res.data if hasattr(res, "data") else res
    except HTTPException:
        raise
    except Exception as e:
        insert_log('consulta_tabla_liq', str(e), 'sistema', 'error')
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/liq")
def create_liq_row(body: dict):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        body = _normalize_date_fields(body)
        result = insert_liq_row(body)
        data = result.data if hasattr(result, "data") else result
        return {"status": "ok", "inserted": data}
    except Exception as e:
        insert_log("insert_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/liq/{escritura}")
def patch_liq_row(escritura: str, body: dict):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        body = _normalize_date_fields(body)
        result = update_liq_row(escritura, body)
        data = result.data if hasattr(result, "data") else result
        return {"status": "ok", "updated": data}
    except Exception as e:
        insert_log("update_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/liq/{escritura}/mark")
def mark_escritura(escritura: str, estado: str, activity_type: str = None):
    try:
        res = update_liq_estado_by_escritura(escritura, estado, activity_type=activity_type)
        data = res.data if hasattr(res, "data") else res
        return {"status": "ok", "updated": data}
    except Exception as e:
        insert_log("update_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/liq/{escritura}/move")
def move_liq(escritura: str, target: str = None):
    try:
        if not target:
            raise HTTPException(status_code=400, detail="Parametro 'target' requerido")
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        res = move_liq_to_table(escritura, target)
        return {"status": "ok", "moved": getattr(res, 'data', None)}
    except HTTPException:
        raise
    except Exception as e:
        insert_log("move_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# PAGOS 2026 & ACTAS
# ==========================================

@app.post("/api/actas/import")
async def import_actas_file(file: UploadFile = File(...), anio: int = 2026):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")

        contents = await file.read()
        nombre = (file.filename or "").lower()
        if nombre.endswith(('.xlsx', '.xls', '.xlsm')):
            df = pd.read_excel(io.BytesIO(contents), dtype=str)
        else:
            text = contents.decode('utf-8-sig', errors='replace')
            sep = ';' if text.split('\n', 1)[0].count(';') > text.split('\n', 1)[0].count(',') else ','
            df = pd.read_csv(io.StringIO(text), dtype=str, sep=sep)

        df.columns = [str(c).strip() for c in df.columns]
        df = df.where(pd.notna(df), None)

        rename_map = {
            'Acta': 'acta', 'Fecha Acta': 'fecha_acta', 'Tipo Deposito': 'tipo_deposito',
            'Depositante': 'cliente', 'Documento': 'documento', 'Radicado': 'radicado',
            'Escritura': 'escritura', 'Valor Acta': 'valor_acta', 'Estado': 'estado',
            'Constructora': 'constructora',
        }
        df = df.rename(columns=rename_map)

        def limpiar(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            s = str(v).strip()
            return s or None

        MESES = {
            'ene': 1, 'jan': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'ago': 8, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12, 'dec': 12,
        }

        def parsear_fecha(raw):
            raw = limpiar(raw)
            if not raw:
                return None

            formats = [
                '%d/%m/%y', '%d/%m/%Y', '%d-%m-%y', '%d-%m-%Y',
                '%d/%b/%Y', '%d/%B/%Y', '%d-%b-%Y', '%d-%B-%Y',
                '%Y-%m-%d', '%Y/%m/%d',
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(raw, fmt).date().isoformat()
                except ValueError:
                    continue

            m = re.match(r'(\d{1,2})/([A-Za-z]{3})/(\d{4})', raw)
            if m:
                dia, mes_txt, anio_txt = m.groups()
                mes = MESES.get(mes_txt.lower())
                if not mes:
                    return None
                try:
                    return datetime(int(anio_txt), mes, int(dia)).date().isoformat()
                except ValueError:
                    return None
            return None

        def parsear_valor(raw):
            raw = limpiar(raw)
            if raw is None:
                return 0
            limpio = re.sub(r'[^\d]', '', raw)
            return int(limpio) if limpio else 0

        tipos_validos = {'Beneficencia', 'Registro Instrumentos Publicos'}
        omitidas_tipo = 0
        omitidas_sin_escritura = 0
        filas = []

        for row in df.to_dict(orient='records'):
            tipo = limpiar(row.get('tipo_deposito')) or ''
            if tipo not in tipos_validos:
                omitidas_tipo += 1
                continue

            esc_match = re.search(r'\d+', limpiar(row.get('escritura')) or '')
            if not esc_match:
                omitidas_sin_escritura += 1
                continue

            filas.append({
                'escritura': int(esc_match.group()),
                'acta': limpiar(row.get('acta')) or '',
                'fecha_acta': parsear_fecha(row.get('fecha_acta')),
                'tipo_deposito': tipo,
                'cliente': limpiar(row.get('cliente')),
                'documento': limpiar(row.get('documento')),
                'radicado': limpiar(row.get('radicado')),
                'anio': anio,
                'valor_acta': parsear_valor(row.get('valor_acta')),
                'constructora': limpiar(row.get('constructora')),
                'estado': limpiar(row.get('estado')),
            })

        if not filas:
            raise HTTPException(
                status_code=400,
                detail="No se encontraron filas válidas para importar."
            )

        result = import_actas_from_rows(filas, batch_size=200)
        result['omitidas_derechos_notariales'] = omitidas_tipo
        result['omitidas_sin_escritura'] = omitidas_sin_escritura

        try:
            insert_log("import_actas", f"Archivo {file.filename}: {result['nuevos']} nuevos", "sistema")
        except Exception:
            pass

        return {"status": "ok", "import_result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pagos2026")
def pagos2026_resumen():
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        return get_pagos_2026_resumen()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pagos2026/{escritura}/actas")
def pagos2026_actas_detalle(escritura: int):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        return get_actas_by_escritura(escritura)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/pagos2026/{escritura}")
def actualizar_pago2026(escritura: int, body: dict):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")

        fecha_pago = body.get('fecha_pago')
        fecha_pago_iso = _format_date_string(fecha_pago) if fecha_pago else None

        result = upsert_pago_2026(
            escritura,
            vr_ben=body.get('vr_ben'),
            vr_reg=body.get('vr_reg'),
            fecha_pago=fecha_pago_iso,
            observaciones=body.get('observaciones'),
            liquidacion=body.get('liquidacion'),
            responsable=body.get('responsable'),
        )
        data = result.data if hasattr(result, "data") else result
        return {"status": "ok", "updated": data}
    except HTTPException:
        raise
    except Exception as e:
        try:
            insert_log("actualizar_pago2026", str(e), "sistema", "error")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/actas")
def crear_acta(body: dict):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        if not body.get('escritura'):
            raise HTTPException(status_code=400, detail="Falta el campo 'escritura'")
        if body.get('tipo_deposito') not in ('Beneficencia', 'Registro Instrumentos Publicos'):
            raise HTTPException(status_code=400, detail="tipo_deposito debe ser 'Beneficencia' o 'Registro Instrumentos Publicos'")
        body.setdefault('anio', 2026)

        result = insert_acta(body)
        data = result.data if hasattr(result, "data") else result
        return {"status": "ok", "inserted": data}
    except HTTPException:
        raise
    except Exception as e:
        try:
            insert_log("crear_acta", str(e), "sistema", "error")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/actas/{acta_id}")
def obtener_acta(acta_id: int):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        response = get_acta_by_id(acta_id)
        if not response:
            raise HTTPException(status_code=404, detail="Acta no encontrada")
        return response
    except HTTPException:
        raise
    except Exception as e:
        try:
            insert_log("obtener_acta", str(e), "sistema", "error")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/actas/{acta_id}")
def actualizar_acta(acta_id: int, body: dict):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        if body.get('tipo_deposito') and body.get('tipo_deposito') not in ('Beneficencia', 'Registro Instrumentos Publicos'):
            raise HTTPException(status_code=400, detail="tipo_deposito debe ser 'Beneficencia' o 'Registro Instrumentos Publicos'")

        result = update_acta(acta_id, body)
        data = result.data if hasattr(result, "data") else result
        return {"status": "ok", "updated": data}
    except HTTPException:
        raise
    except Exception as e:
        try:
            insert_log("actualizar_acta", str(e), "sistema", "error")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/actas/{acta_id}")
def borrar_acta(acta_id: int):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")

        result = delete_acta(acta_id)
        data = result.data if hasattr(result, "data") else result
        return {"status": "ok", "deleted": data}
    except HTTPException:
        raise
    except Exception as e:
        try:
            insert_log("borrar_acta", str(e), "sistema", "error")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pagos2026/informe", response_class=HTMLResponse)
def generar_informe_pagos(fecha_pago: Optional[str] = None):
    try:
        db = get_supabase()
        if not db:
            raise HTTPException(status_code=503, detail="Supabase no configurado.")

        fecha_filtro = None
        fecha_mostrar = datetime.now().strftime('%d/%m/%Y')

        if fecha_pago:
            fecha_pago = fecha_pago.strip()
            if "/" in fecha_pago:
                partes = fecha_pago.split("/")
                if len(partes) == 3:
                    if len(partes[0]) == 2 and len(partes[2]) == 4:
                        fecha_filtro = f"{partes[2]}-{partes[1]}-{partes[0]}"
                        fecha_mostrar = f"{partes[0]}/{partes[1]}/{partes[2]}"
                    elif len(partes[0]) == 4:
                        fecha_filtro = f"{partes[0]}-{partes[1]}-{partes[2]}"
                        fecha_mostrar = f"{partes[2]}/{partes[1]}/{partes[0]}"
            elif "-" in fecha_pago:
                partes = fecha_pago.split("-")
                if len(partes) == 3:
                    if len(partes[0]) == 4:
                        fecha_filtro = fecha_pago
                        fecha_mostrar = f"{partes[2]}/{partes[1]}/{partes[0]}"
                    elif len(partes[2]) == 4:
                        fecha_filtro = f"{partes[2]}-{partes[1]}-{partes[0]}"
                        fecha_mostrar = f"{partes[0]}/{partes[1]}/{partes[2]}"

        actas_resp = db.table('actas') \
            .select('id, escritura, acta, cliente, documento, radicado, constructora, estado, valor_acta') \
            .order('id', desc=False) \
            .execute()

        actas_data = actas_resp.data or []

        mapa_actas = {}
        for a in actas_data:
            esc_k = str(normalizar_escritura(a.get('escritura'))).strip()
            if not esc_k:
                continue

            doc_actual = str(a.get("documento") or "").strip()
            doc_actual = "" if doc_actual in ["None", "NaN"] else doc_actual

            cli_actual = str(a.get("cliente") or "").strip()
            cli_actual = "" if cli_actual in ["None", "NaN"] else cli_actual

            rad_actual = str(a.get("radicado") or "").strip()
            rad_actual = "" if rad_actual in ["None", "NaN"] else rad_actual

            con_actual = str(a.get("constructora") or "").strip()
            con_actual = "" if con_actual in ["None", "NaN"] else con_actual

            est_actual = str(a.get("estado") or "").strip()
            est_actual = "" if est_actual in ["None", "NaN"] else est_actual

            if esc_k not in mapa_actas:
                mapa_actas[esc_k] = {
                    "actas": [],
                    "cliente": cli_actual,
                    "documento": doc_actual,
                    "radicado": rad_actual,
                    "constructora": con_actual,
                    "estado": est_actual,
                    "pago_cli": 0.0
                }
            else:
                if not mapa_actas[esc_k]["cliente"] and cli_actual:
                    mapa_actas[esc_k]["cliente"] = cli_actual
                if not mapa_actas[esc_k]["documento"] and doc_actual:
                    mapa_actas[esc_k]["documento"] = doc_actual
                if not mapa_actas[esc_k]["radicado"] and rad_actual:
                    mapa_actas[esc_k]["radicado"] = rad_actual
                if not mapa_actas[esc_k]["constructora"] and con_actual:
                    mapa_actas[esc_k]["constructora"] = con_actual
                if not mapa_actas[esc_k]["estado"] and est_actual:
                    mapa_actas[esc_k]["estado"] = est_actual

            val_acta = float(a.get('valor_acta') or 0)
            mapa_actas[esc_k]["pago_cli"] += val_acta

            acta_num = str(a.get("acta") or "").strip()
            if acta_num and acta_num not in mapa_actas[esc_k]["actas"]:
                mapa_actas[esc_k]["actas"].append(acta_num)

        query = db.table("pagos_2026").select("*")
        if fecha_filtro:
            query = query.eq("fecha_pago", fecha_filtro)
            
        pagos_resp = query.execute()
        registros_pagos = pagos_resp.data or []

        pagos_por_escritura = {
            str(normalizar_escritura(item.get("escritura"))).strip(): item
            for item in registros_pagos
            if item.get('escritura') is not None
        }

        def clave_orden_escritura(item):
            esc_str = str(item.get("escritura", "")).strip()
            solo_numeros = ''.join(c for c in esc_str if c.isdigit())
            return int(solo_numeros) if solo_numeros else 0

        registros_pagos.sort(key=clave_orden_escritura)

        result_liq = db.table('liq').select('escritura, nir, responsable').execute()

        mapa_liq = {}
        if result_liq.data:
            for item in result_liq.data:
                esc_k = str(normalizar_escritura(item.get("escritura"))).strip()
                if esc_k:
                    mapa_liq[esc_k] = item

        tot_ben = 0.0
        tot_reg = 0.0
        tot_recibo = 0.0
        tot_pago_cli = 0.0
        tot_faltante = 0.0
        tot_sobrante = 0.0

        filas_html = ""

        for item in registros_pagos:
            esc_raw = item.get("escritura")
            esc = str(normalizar_escritura(esc_raw)).strip()

            datos_acta = mapa_actas.get(esc, {})
            datos_liq = mapa_liq.get(esc, {})
            datos_pago = pagos_por_escritura.get(esc, {})

            resp = item.get("responsable") or datos_liq.get("responsable") or "Venta"
            liq_val = str(datos_pago.get("liquidacion") or "").strip()
            nir_val = str(datos_liq.get("nir") or "").strip()        

            actas_list = datos_acta.get("actas", [])
            actas_str = "-".join(actas_list) if actas_list else str(item.get("actas") or "")

            contrib = str(datos_acta.get("cliente") or "").strip()
            doc_raw = str(datos_acta.get("documento") or item.get("documento") or "").strip()
            identif = re.sub(r'[^0-9]', '', doc_raw) if ("C.C." in doc_raw or "N.I.T." in doc_raw) else doc_raw

            vr_ben = float(item.get("vr_ben") or 0)
            vr_reg = float(item.get("vr_reg") or 0)
            total_recibo = vr_ben + vr_reg

            pago_cli = datos_acta.get("pago_cli", 0.0)

            faltante = max(total_recibo - pago_cli, 0.0)
            sobrante = max(pago_cli - total_recibo, 0.0)

            tot_ben += vr_ben
            tot_reg += vr_reg
            tot_recibo += total_recibo
            tot_pago_cli += pago_cli
            tot_faltante += faltante
            tot_sobrante += sobrante

            filas_html += f"""
            <tr>
                <td class="center">{esc_raw or esc}</td>
                <td class="center">{resp}</td>
                <td class="center">{liq_val}</td>
                <td class="center">{nir_val}</td>
                <td class="center">{actas_str}</td>
                <td class="right">$ {vr_ben:,.0f}</td>
                <td class="right">$ {vr_reg:,.0f}</td>
                <td class="right font-bold">$ {total_recibo:,.0f}</td>
                <td class="right font-bold">$ {pago_cli:,.0f}</td>
                <td class="right text-red">{f'$ {faltante:,.0f}' if faltante > 0 else '-'}</td>
                <td class="right text-green">{f'$ {sobrante:,.0f}' if sobrante > 0 else '-'}</td>
                <td class="center">{contrib}</td>
                <td class="center">{identif}</td>
            </tr>
            """

        html_content = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
            <title>Informe General de Pagos</title>
            <style>
                @page {{ size: landscape; margin: 8mm; }}
                body {{ font-family: Arial, sans-serif; font-size: 11px; margin: 0; padding: 10px; background-color: #fff; }}
                .header-container {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; position: relative; }}
                .header-logo img {{ max-height: 80px; width: auto; }}
                .no-print {{ margin-bottom: 15px; text-align: right; }}
                .btn-print {{ background: #1e293b; color: #fff; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }}
                .main-table {{ width: 100%; border-collapse: collapse; font-size: 10.5px; }}
                .main-table th {{ background-color: #e5e7eb; color: #000; border: 1px solid #000; padding: 5px 3px; font-weight: bold; text-align: center; white-space: nowrap; }}
                .main-table td {{ border: 1px solid #71717a; padding: 4px 5px; white-space: nowrap; }}
                .main-table tfoot td {{ background-color: #f3f4f6; border: 1px solid #000; border-top: 2px solid #000; border-bottom: 3px double #000; font-weight: bold; padding: 6px 5px; }}
                .center {{ text-align: center; }}
                .right {{ text-align: right; }}
                .font-bold {{ font-weight: bold; }}
                .text-red {{ color: #dc2626; }}
                .text-green {{ color: #16a34a; }}
                @media print {{ .no-print {{ display: none; }} }}
            </style>
        </head>
        <body>
            <div class="no-print">
                <button class="btn-print" onclick="window.print()">🖨️ Generar</button>                
            </div>
            <div class="header-container">
                <div class="header-logo" style="flex: 1; text-align: left;">
                    <img src="/static/logo.jpg" alt="Logo Notaría" />
                </div>
                <div style="flex: 2; text-align: center; margin: 0;">
                    <span style="font-size: 26px; display: block;">INFORME GENERAL DE PAGOS</span>
                </div>
                <div style="flex: 1; text-align: right; margin: 0;">
                    <span style="font-size: 22px; display: inline-block; padding: 4px 12px; background: #e5e7eb; border-radius: 4px;">
                        {fecha_mostrar}
                    </span>
                </div>                               
            </div>
            <table class="main-table">
                <thead>
                    <tr>
                        <th>Escritura</th>
                        <th>Responsable</th>
                        <th>Liquidación</th>
                        <th>NIR</th>
                        <th>Actas</th>
                        <th>Vr_Ben</th>
                        <th>Vr_Reg</th>
                        <th>Total Recibo</th>
                        <th>Pago_Cli</th>
                        <th>Faltante</th>
                        <th>Sobrante</th>
                        <th>Cliente</th>
                        <th>Documento</th>
                    </tr>
                </thead>
                <tbody>
                    {filas_html}
                </tbody>
                <tfoot>
                    <tr>
                        <td colspan="5" class="left font-bold">TOTALES / SUMA:</td>
                        <td class="right font-bold">$ {tot_ben:,.0f}</td>
                        <td class="right font-bold">$ {tot_reg:,.0f}</td>
                        <td class="right font-bold">$ {tot_recibo:,.0f}</td>
                        <td class="right font-bold">$ {tot_pago_cli:,.0f}</td>
                        <td class="right font-bold text-red">$ {tot_faltante:,.0f}</td>
                        <td class="right font-bold text-green">$ {tot_sobrante:,.0f}</td>
                        <td colspan="2"></td>
                    </tr>
                </tfoot>
            </table>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)    

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# GESTIÓN DE EXCEL & EXPORTACIÓN
# ==========================================

@app.post("/api/import/excel")
async def import_excel_file(file: UploadFile = File(...), table: str = "liq"):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        
        allowed_tables = {
            "liq": "liq",
            "pagos": "pagos_2026",
            "pagos_consolidado": "pagos_consolidado",
        }
        if table not in allowed_tables:
            raise HTTPException(status_code=400, detail=f"Tabla no válida: {table}")

        contents = await file.read()
        if file.filename.lower().endswith(('.xlsx', '.xls')):
            excel_file = io.BytesIO(contents)
            df = pd.read_excel(excel_file, dtype=str)
            csv_text = df.to_csv(index=False, encoding='utf-8')
        else:
            text = contents.decode('utf-8', errors='replace')
            df = pd.read_csv(io.StringIO(text), dtype=str)
            csv_text = text

        df = df.where(pd.notna(df), None)
        df = normalize_import_columns(df)

        if df.empty:
            raise HTTPException(status_code=400, detail="El archivo está vacío.")

        basic_cols = ["escritura", "nir", "correo", "gobernacion"]
        default_pending = {
            "pago": "Ingresado",
            "estado_ctl": "Pendiente",
            "notificacion": "Pendiente",
            "devolucion": "",
        }

        rows = df.to_dict(orient="records")
        cleaned_rows = []
        for row in rows:
            base = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k in basic_cols and v is not None and (not isinstance(v, str) or v.strip() != "")}
            if not all(col in base and base[col] for col in ["escritura", "nir", "correo", "gobernacion"]):
                continue
            for k, v in default_pending.items():
                base.setdefault(k, v)
            cleaned_rows.append(base)

        target_table = allowed_tables[table]
        if table == "liq":
            result = import_liq_from_rows(cleaned_rows, batch_size=100)
        elif table == "pagos":
            result = import_pagos_from_rows(cleaned_rows, batch_size=100)
        else:
            result = import_pagos_consolidado_from_rows(cleaned_rows, batch_size=100)

        insert_log("import_excel", f"Archivo {file.filename} importado en tabla {target_table}: {result['nuevos']} nuevos", "sistema")

        return {
            "status": "ok",
            "import_result": result,
            "csv_preview": csv_text.splitlines()[:5],
            "target_table": target_table,
        }
    except HTTPException:
        raise
    except Exception as e:
        insert_log("import_excel_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=f"Error al procesar archivo: {str(e)}")

@app.post('/api/export/liq')
def export_liq_backup():
    try:
        ruta = export_liq_to_excel()
        insert_log('export_liq', f'Backup generado: {ruta}', 'sistema')
        return {"status": "ok", "path": ruta}
    except Exception as e:
        insert_log('export_liq_error', str(e), 'sistema', 'error')
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/export/liq/download')
def download_liq_backup():
    try:
        ruta = export_liq_to_excel()
        insert_log('download_liq', f'Backup descargado: {ruta}', 'sistema')
        return FileResponse(ruta, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', filename=os.path.basename(ruta))
    except Exception as e:
        insert_log('download_liq_error', str(e), 'sistema', 'error')
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# ENVÍOS / DESCARGAS Y CERTIFICADOS
# ==========================================

@app.post("/api/descargas/guardar")
async def guardar_descarga_endpoint(
    tipo: str,
    escritura: str,
    file: UploadFile = File(...),
    email: str = None
):
    try:
        if tipo not in ['recibo', 'certificado']:
            raise HTTPException(status_code=400, detail="Tipo debe ser 'recibo' o 'certificado'")
        
        contenido = await file.read()
        resultado = guardar_descarga(
            tipo=tipo,
            escritura=escritura,
            archivo_nombre=file.filename,
            archivo_contenido=contenido,
            email=email
        )
        insert_log("guardar_descarga", f"{tipo} {escritura} guardado: {file.filename}", "sistema")
        return {
            "status": "ok",
            "descarga_id": resultado.data[0]['id'] if resultado.data else None,
            "mensaje": f"{tipo.capitalize()} guardado exitosamente"
        }
    except HTTPException:
        raise
    except Exception as e:
        insert_log("guardar_descarga_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/descargas/{escritura}")
def obtener_descargas(escritura: str, tipo: str = None):
    try:
        resultado = obtener_descargas_por_escritura(escritura, tipo)
        descargas = []
        for desc in resultado.data:
            descarga_dict = desc.copy()
            if 'archivo_contenido' in descarga_dict and descarga_dict['archivo_contenido']:
                descarga_dict['archivo_base64'] = base64.b64encode(descarga_dict['archivo_contenido']).decode()
                del descarga_dict['archivo_contenido']
            descargas.append(descarga_dict)
        return {"status": "ok", "descargas": descargas}
    except Exception as e:
        insert_log("obtener_descargas_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/descargas/download/{descarga_id}")
def descargar_archivo(descarga_id: str):
    try:
        supabase = get_supabase()
        resultado = supabase.table("descargas").select("*").eq("id", descarga_id).execute()
        if not resultado.data:
            raise HTTPException(status_code=404, detail="Archivo no encontrado")
        
        descarga = resultado.data[0]
        contenido = descarga.get('archivo_contenido')
        nombre = descarga.get('archivo_nombre', 'descarga.pdf')
        
        if not contenido:
            raise HTTPException(status_code=404, detail="Contenido no disponible")
        
        return FileResponse(io.BytesIO(contenido), media_type='application/octet-stream', filename=nombre)
    except HTTPException:
        raise
    except Exception as e:
        insert_log("descargar_archivo_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/descargas/{descarga_id}/enviar-correo")
def enviar_descarga_por_correo(descarga_id: str, body: dict):
    try:
        supabase = get_supabase()
        resultado = supabase.table("descargas").select("*").eq("id", descarga_id).execute()

        if not resultado.data:
            raise HTTPException(status_code=404, detail="Archivo no encontrado")

        descarga = resultado.data[0]
        email_destino = (body or {}).get('email') or descarga.get('email')
        contenido = descarga.get('archivo_contenido')
        nombre = descarga.get('archivo_nombre', 'documento.pdf')
        tipo = descarga.get('tipo', 'documento')
        escritura = descarga.get('escritura')

        if not email_destino or not contenido:
            raise HTTPException(status_code=400, detail="Falta email de destino o el archivo no tiene contenido")

        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        smtp_from = os.getenv("SMTP_FROM") or smtp_user

        if not smtp_host or not smtp_user or not smtp_pass:
            raise HTTPException(status_code=503, detail="SMTP no configurado.")

        msg = EmailMessage()
        msg["Subject"] = f"{tipo.capitalize()} {escritura}"
        msg["From"] = smtp_from
        msg["To"] = email_destino
        msg.set_content(f"Adjunto {tipo} {escritura}")

        contenido_bytes = contenido.tobytes() if isinstance(contenido, memoryview) else contenido
        msg.add_attachment(contenido_bytes, maintype="application", subtype="pdf", filename=nombre)

        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()

        marcar_descarga_como_enviada(descarga_id)
        insert_log("envio_descarga", f"Envío de {tipo} {escritura} a {email_destino}", "sistema")

        return {"status": "ok", "mensaje": f"{tipo.capitalize()} enviado a {email_destino}"}
    except HTTPException:
        raise
    except Exception as e:
        insert_log("envio_descarga_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/descargas/pendientes")
def obtener_descargas_pendientes(tipo: str = None, limit: int = 100):
    try:
        resultado = obtener_descargas_pendientes_de_envio(tipo, limit)
        descargas = []
        for desc in resultado.data:
            descarga_dict = desc.copy()
            if 'archivo_contenido' in descarga_dict:
                del descarga_dict['archivo_contenido']
            descargas.append(descarga_dict)
        return {"status": "ok", "total": len(descargas), "descargas": descargas}
    except Exception as e:
        insert_log("obtener_pendientes_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/envios/devoluciones/start")
def start_devoluciones():
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}

    def _run(job_id):
        try:
            JOBS[job_id]["status"] = "running"
            script_path = os.path.join(APP_DIR, "prueba_devolucion.py")
            proc = subprocess.Popen(
                [sys.executable, script_path],
                cwd=APP_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in proc.stdout:
                _append(job_id, line)
            proc.wait()
            JOBS[job_id]["returncode"] = proc.returncode
            JOBS[job_id]["status"] = "done" if proc.returncode == 0 else "error"
        except Exception as e:
            JOBS[job_id]["status"] = "error"
            _append(job_id, f"[EXCEPTION] {type(e).__name__}: {e}")

    t = threading.Thread(target=_run, args=(job_id,), daemon=True)
    t.start()
    return {"job_id": job_id}

# ==========================================
# MAIN EXECUTION
# ==========================================

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
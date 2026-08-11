import os
import sys
import uuid
import threading
import subprocess
import io
from pathlib import Path
import base64
import smtplib
from email.message import EmailMessage
import pandas as pd
import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from repositories.liq_repository import LiqRepository
from services.workflow_service import WorkflowService
load_dotenv()
try:
    # Intenta importación relativa (cuando se usa como paquete)
    from database.supabase_client import (
        insert_log,
        get_supabase,
        check_table_exists,
        insert_certificado,
        update_certificado_estado,
        get_certificados_por_usuario,
        insert_recibo,
        guardar_descarga,
        obtener_descargas_por_escritura,
        obtener_descargas_pendientes_de_envio,
        marcar_descarga_como_enviada,
        import_liq_from_rows,
        import_pagos_from_rows,
        import_pagos_consolidado_from_rows,
    )
    from database.supabase_client import insert_liq_row, update_liq_row, get_pending_liq, update_liq_estado_by_escritura
    from database.supabase_client import get_liq_stats
    from database.supabase_client import get_all_liq, get_processed_liq
    from database.supabase_client import move_liq_to_table, export_liq_to_excel
    from database.supabase_client import get_table_rows
except ImportError:
    # Fallback a importación absoluta (cuando se ejecuta directamente con uvicorn)
    from database.supabase_client import (
        insert_log,
        get_supabase,
        check_table_exists,
        insert_certificado,
        update_certificado_estado,
        get_certificados_por_usuario,
        insert_recibo,
        guardar_descarga,
        obtener_descargas_por_escritura,
        obtener_descargas_pendientes_de_envio,
        marcar_descarga_como_enviada,
        import_liq_from_rows,
        import_pagos_from_rows,
        import_pagos_consolidado_from_rows,
    )
    from database.supabase_client import insert_liq_row, update_liq_row, get_pending_liq, update_liq_estado_by_escritura
    from database.supabase_client import get_liq_stats
    from database.supabase_client import get_all_liq, get_processed_liq
    from database.supabase_client import move_liq_to_table, export_liq_to_excel
    from database.supabase_client import get_table_rows

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
HTML_FILE = os.path.join(STATIC_DIR, "notaria_app.html")
if not os.path.exists(HTML_FILE):
    HTML_FILE = os.path.join(APP_DIR, "notaria_app.html")
SQL_SCHEMA_FILE = Path(APP_DIR, "supabase_schema_from_excel.sql")

app = FastAPI()

# Servir tu HTML tal cual
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

JOBS = {}  # job_id -> {"status": "running|done|error", "logs": [str], "returncode": int|None}

def _append(job_id: str, line: str):
    JOBS[job_id]["logs"].append(line.rstrip())

def load_schema_sql() -> str:
    """Carga el archivo de esquema SQL."""
    if not SQL_SCHEMA_FILE.exists():
        raise RuntimeError(f"No se encontró el archivo de esquema: {SQL_SCHEMA_FILE}")
    return SQL_SCHEMA_FILE.read_text(encoding="utf-8")

def _apply_schema_sql(db_url: str, sql: str) -> None:
    """Aplica DDL SQL directamente a PostgreSQL."""
    try:
        import psycopg # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Instala psycopg con `pip install psycopg[binary]` para ejecutar DDL directamente."
        ) from exc

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

def _get_schema_validation() -> dict:
    """Valida que existan las tablas requeridas en Supabase."""
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
    """Normaliza encabezados de Excel/CSV para importación."""
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

def _run_script_job(job_id: str, script_name: str, args: list = None):
    """Helper genérico para ejecutar scripts Python en background y capturar logs."""
    try:
        JOBS[job_id]["status"] = "running"
        script_path = os.path.join(APP_DIR, script_name)

        cmd = [sys.executable, script_path]
        if args:
            cmd.extend(args)

        proc = subprocess.Popen(
            cmd,
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
        if proc.returncode == 0:
            JOBS[job_id]["status"] = "done"
        else:
            JOBS[job_id]["status"] = "error"
            _append(job_id, f"[ERROR] {script_name} terminó con código {proc.returncode}")

    except Exception as e:
        JOBS[job_id]["status"] = "error"
        _append(job_id, f"[EXCEPTION] {type(e).__name__}: {e}")

@app.get('/api/dashboard/status')
def dashboard_status():
    try:
        repo = LiqRepository()

        pendientes_certificados = len(
            repo.certificados_pendientes()
        )
        procesando = len(
            repo.certificados_procesando()
        )
        total = (
            repo.supabase
            .table('liq')
            .select('*', count='exact')
            .execute()
        )
        pendientes_recibos = (
            repo.supabase
            .table('liq')
            .select('*', count='exact')
            .eq('notificacion', 'pendiente')
            .execute()
        )
        recibos_enviados = (
            repo.supabase
            .table('liq')
            .select('*', count='exact')
            .eq('notificacion', 'enviado')
            .execute()
        )
        certificados_enviados = (
            repo.supabase
            .table('liq')
            .select('*', count='exact')
            .eq('estado_ctl', 'certificado')
            .execute()
        )
        errores = (
            repo.supabase
            .table('logss')
            .select('*', count='exact')
            .eq('estado', 'error')
            .execute()
        )

        return {
            'total_registros': total.count or 0,
            'pendientes_certificados': pendientes_certificados,
            'procesando': procesando,
            'recibos_pendientes': pendientes_recibos.count or 0,
            'recibos_enviados': recibos_enviados.count or 0,
            'certificados_enviados': certificados_enviados.count or 0,
            'errores': errores.count or 0
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al obtener estado del dashboard: {str(e)}"
        )

@app.get("/", response_class=HTMLResponse)
def home():
    if not os.path.exists(HTML_FILE):
        raise HTTPException(status_code=404, detail="No se encontró la interfaz principal")
    with open(HTML_FILE, "r", encoding="utf-8") as f:
        return f.read()

@app.get("/health")
def health():
    return {"status": "ok", "service": "notaria"}

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


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id no existe")
    return job

@app.get("/api/supabase/schema/validate")
def validate_supabase_schema():
    """Valida que existan las tablas requeridas en Supabase."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        return _get_schema_validation()
    except HTTPException:
        raise
    except Exception as e:
        insert_log("validate_schema_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/supabase/schema/apply")
def apply_supabase_schema():
    """Aplica el esquema SQL automáticamente."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")

        db_url = os.getenv("SUPABASE_DB_URL")
        if not db_url:
            raise HTTPException(
                status_code=400,
                detail="SUPABASE_DB_URL no está configurada. Establece SUPABASE_DB_URL en .env para aplicar esquema automáticamente.",
            )

        sql = load_schema_sql()
        _apply_schema_sql(db_url, sql)
        schema_status = _get_schema_validation()
        return {"status": "ok", "message": "SQL aplicado correctamente.", "schema_status": schema_status}
    except HTTPException:
        raise
    except Exception as e:
        insert_log("apply_schema_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/liq/pending")
def listar_pending(limit: int = 10000, page: int = 1, sort_by: str = 'escritura', desc: bool = False):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        res = get_pending_liq(limit=limit, page=page, sort_by=sort_by, desc=desc)
        return res.data
    except Exception as e:
        insert_log("consulta_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/liq/stats")
def liq_stats():
    """Devuelve estadísticas agregadas para la UI: total, pendientes y procesados."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        stats = get_liq_stats()
        return stats
    except Exception as e:
        try:
            insert_log("stats_liq", str(e), "sistema", "error")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/liq/all")
def liq_all(limit: int = 10000, page: int = 1, sort_by: str = 'updated_at', desc: bool = True):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        # Limitar el tamaño de página para evitar rangos excesivos en Supabase.
        max_limit = min(max(limit, 1), 10000)
        res = get_all_liq(limit=max_limit, page=max(page, 1), sort_by=sort_by, desc=desc)
        return res.data
    except Exception as e:
        insert_log("consulta_liq_all", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/liq")
def create_liq_row(body: dict):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        result = insert_liq_row(body)
        return {"status": "ok", "inserted": result.data}
    except Exception as e:
        insert_log("insert_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/liq/{escritura}")
def patch_liq_row(escritura: str, body: dict):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        result = update_liq_row(escritura, body)
        return {"status": "ok", "updated": result.data}
    except Exception as e:
        insert_log("update_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/liq/processed")
def liq_processed(limit: int = 10000, page: int = 1, sort_by: str = 'fecha_proceso', desc: bool = True):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        res = get_processed_liq(limit=limit, page=page, sort_by=sort_by, desc=desc)
        return res.data
    except Exception as e:
        insert_log("consulta_liq_processed", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/liq/table/{table_name}')
def liq_table(table_name: str, limit: int = 1000, page: int = 1, sort_by: str = None, desc: bool = True):
    """Devuelve filas de tablas `liq` específicas (solo listas permitidas)."""
    try:
        allowed = {'liq', 'liq_2025', 'liq_2026', 'pagos_2026', 'pagos_consolidado'}
        if table_name not in allowed:
            raise HTTPException(status_code=400, detail=f"Tabla no permitida: {table_name}")
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        res = get_table_rows(table_name, limit=limit, page=page, sort_by=sort_by, desc=desc)
        return res.data
    except HTTPException:
        raise
    except Exception as e:
        insert_log('consulta_tabla_liq', str(e), 'sistema', 'error')
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/liq/{escritura}/mark")
def mark_escritura(escritura: str, estado: str, activity_type: str = None):
    try:
        res = update_liq_estado_by_escritura(escritura, estado, activity_type=activity_type)
        return {"status": "ok", "updated": res.data}
    except Exception as e:
        insert_log("update_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/liq/{escritura}/move")
def move_liq(escritura: str, target: str = None):
    """Mueve un registro entre tablas liq. Parámetro query: target (nombre de tabla destino)."""
    try:
        if not target:
            raise HTTPException(status_code=400, detail="Parametro 'target' requerido")
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        res = move_liq_to_table(escritura, target)
        return {"status": "ok", "moved": getattr(res, 'data', None)}
    except HTTPException:
        raise
    except Exception as e:
        insert_log("move_liq", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))
# Endpoints para Supabase
@app.get("/api/certificados")
def listar_certificados(usuario: str = None):
    try:
        supabase = get_supabase()
        if usuario:
            result = supabase.table("certificados").select("*").eq("usuario", usuario).execute()
            return result.data
        result = supabase.table("certificados").select("*").execute()
        return result.data
    except Exception as e:
        insert_log("consulta_certificados", str(e), usuario or "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/certificados/{cert_id}")
def obtener_certificado(cert_id: str):
    try:
        supabase = get_supabase()
        result = supabase.table("certificados").select("*").eq("id", cert_id).execute()
        if not result.data:
            raise HTTPException(status_code=404, detail="Certificado no encontrado")
        return result.data[0]
    except HTTPException:
        raise
    except Exception as e:
        insert_log("consulta_certificado", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

# --- agrega estas funciones/endpoint a tu app.py ---

def _run_envio_recibos_job(job_id: str):
    try:
        JOBS[job_id]["status"] = "running"
        workflow = WorkflowService()
        workflow.ejecutar_recibos()
        JOBS[job_id]["returncode"] = 0
        JOBS[job_id]["status"] = "done"
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        _append(job_id, f"[EXCEPTION] {type(e).__name__}: {e}")

@app.post("/api/envios/recibos/start")
def start_envio_recibos():
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}
    t = threading.Thread(target=_run_envio_recibos_job, args=(job_id,), daemon=True)
    t.start()
    return {"job_id": job_id}

@app.post("/api/envios/recibos/unico/start")
def start_envio_recibo_unico(body: dict):
    """Recibe un payload JSON para enviar un único recibo.
    Crea un archivo temporal con el payload y lanza envio_recibos.py --single <file> en hilo.
    """
    if not isinstance(body, dict) or not body.get("escritura"):
        raise HTTPException(status_code=400, detail="Escritura es obligatoria")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}

    def _run_unico(job_id, payload):
        try:
            JOBS[job_id]["status"] = "running"
            # crear archivo temporal con payload
            import json, tempfile
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=APP_DIR, mode='w', encoding='utf-8')
            json.dump(payload, tf, ensure_ascii=False)
            tf.close()
            script_path = os.path.join(APP_DIR, "envio_recibos.py")
            proc = subprocess.Popen(
                [sys.executable, script_path, "--single", tf.name],
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

    t = threading.Thread(target=_run_unico, args=(job_id, body), daemon=True)
    t.start()
    return {"job_id": job_id}

def _run_envio_certificados_job(job_id: str):
    try:
        JOBS[job_id]["status"] = "running"
        workflow = WorkflowService()
        workflow.ejecutar_envio_certificados()
        JOBS[job_id]["returncode"] = 0
        JOBS[job_id]["status"] = "done"
    except Exception as e:
        JOBS[job_id]["status"] = "error"
        _append(job_id, f"[EXCEPTION] {type(e).__name__}: {e}")

@app.post("/api/envios/certificados/start")
def start_envio_certificados():
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}
    t = threading.Thread(target=_run_envio_certificados_job, args=(job_id,), daemon=True)
    t.start()
    return {"job_id": job_id}

@app.post("/api/envios/certificados/unico/start")
def start_envio_certificado_unico(body: dict):
    if not isinstance(body, dict) or not body.get("escritura"):
        raise HTTPException(status_code=400, detail="Escritura es obligatoria")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "queued", "logs": [], "returncode": None}

    def _run_unico(job_id, payload):
        try:
            JOBS[job_id]["status"] = "running"
            import json, tempfile
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".json", dir=APP_DIR, mode='w', encoding='utf-8')
            json.dump(payload, tf, ensure_ascii=False)
            tf.close()

            script_path = os.path.join(APP_DIR, "envio_certificados.py")
            proc = subprocess.Popen(
                [sys.executable, script_path, "--single", tf.name],
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

    t = threading.Thread(target=_run_unico, args=(job_id, body), daemon=True)
    t.start()
    return {"job_id": job_id}

@app.post("/api/import/excel")
async def import_excel_file(file: UploadFile = File(...), table: str = "liq"):
    """
    Carga un archivo Excel dinámicamente y lo importa en Supabase.
    Solo carga registros nuevos (detecta duplicados por escritura).
    Soporta: liq, pagos (pagos_2026), pagos_consolidado
    """
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        
        # Mapeo de nombres de tabla
        allowed_tables = {
            "liq": "liq",
            "pagos": "pagos_2026",
            "pagos_consolidado": "pagos_consolidado",
        }
        if table not in allowed_tables:
            raise HTTPException(status_code=400, detail=f"Tabla no válida. Permitidas: {list(allowed_tables.keys())}")

        # Leer archivo Excel/CSV
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
            raise HTTPException(status_code=400, detail="El archivo está vacío o no contiene filas válidas.")

        if table == 'liq' and not any('escritura' in col for col in df.columns):
            raise HTTPException(status_code=400, detail="El archivo debe contener la columna 'Escritura' (o equivalente).")

        # Solo columnas básicas
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
            # Solo tomar los campos básicos y rellenar el resto
            base = {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k in basic_cols and v is not None and (not isinstance(v, str) or v.strip() != "")}
            # Si falta alguno de los básicos, no lo subas
            if not all(col in base and base[col] for col in ["escritura", "nir", "correo", "gobernacion"]):
                continue
            # Rellenar el resto
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

        # Registrar en logs
        insert_log("import_excel", f"Archivo {file.filename} importado en tabla {target_table}: {result['nuevos']} nuevos", "sistema")

        preview_lines = csv_text.splitlines()[:5]
        return {
            "status": "ok",
            "import_result": result,
            "csv_preview": preview_lines,
            "target_table": target_table,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        insert_log("import_excel_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=f"Error al procesar archivo: {str(e)}")

# ========================
# NUEVOS ENDPOINTS PARA DESCARGAS Y ENVÍOS
# ========================

@app.post("/api/descargas/guardar")
async def guardar_descarga_endpoint(
    tipo: str,
    escritura: str,
    file: UploadFile = File(...),
    email: str = None
):
    """
    Endpoint para guardar un archivo descargado (certificado o recibo).
    
    Args:
        tipo: 'recibo' o 'certificado'
        escritura: número de escritura
        file: archivo a guardar
        email: email del destinatario (opcional)
    """
    try:
        if tipo not in ['recibo', 'certificado']:
            raise HTTPException(status_code=400, detail="Tipo debe ser 'recibo' o 'certificado'")
        
        # Leer contenido del archivo
        contenido = await file.read()
        
        # Guardar en Supabase
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
    """
    Obtiene todas las descargas para una escritura.
    
    Args:
        escritura: número de escritura
        tipo: 'recibo' o 'certificado' (opcional)
    """
    try:
        resultado = obtener_descargas_por_escritura(escritura, tipo)
        
        # Convertir archivos a base64 para respuesta JSON
        descargas = []
        for desc in resultado.data:
            descarga_dict = desc.copy()
            if 'archivo_contenido' in descarga_dict and descarga_dict['archivo_contenido']:
                # Ya está en bytes, convertir a base64
                descarga_dict['archivo_base64'] = base64.b64encode(descarga_dict['archivo_contenido']).decode()
                del descarga_dict['archivo_contenido']  # No enviar contenido binario en JSON
            descargas.append(descarga_dict)
        
        return {"status": "ok", "descargas": descargas}
    
    except Exception as e:
        insert_log("obtener_descargas_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/descargas/download/{descarga_id}")
def descargar_archivo(descarga_id: str):
    """
    Descarga un archivo específico por ID.
    
    Args:
        descarga_id: UUID de la descarga
    """
    try:
        supabase = get_supabase()
        resultado = supabase.table("descargas").select("*").eq("id", descarga_id).execute()
        
        if not resultado.data:
            raise HTTPException(status_code=404, detail="Archivo no encontrado")
        
        descarga = resultado.data[0]
        contenido = descarga.get('archivo_contenido')
        nombre = descarga.get('archivo_nombre', 'descarga.pdf')
        
        if not contenido:
            raise HTTPException(status_code=404, detail="Contenido del archivo no disponible")
        
        # Retornar como descarga
        return FileResponse(
            io.BytesIO(contenido),
            media_type='application/octet-stream',
            filename=nombre
        )
    
    except HTTPException:
        raise
    except Exception as e:
        insert_log("descargar_archivo_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/descargas/{descarga_id}/enviar-correo")
def enviar_descarga_por_correo(descarga_id: str, body: dict):
    """
    Envía un archivo descargado por correo electrónico.
    
    Args:
        descarga_id: UUID de la descarga
        body: {'email': 'destinatario@ejemplo.com'} (opcional, usa el del registro)
    """
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

        if not email_destino:
            raise HTTPException(status_code=400, detail="Email de destinatario no especificado")

        if not contenido:
            raise HTTPException(status_code=400, detail="Contenido del archivo no disponible")

        # SMTP configuration from environment
        smtp_host = os.getenv("SMTP_HOST")
        smtp_port = int(os.getenv("SMTP_PORT", 587))
        smtp_user = os.getenv("SMTP_USER")
        smtp_pass = os.getenv("SMTP_PASS")
        smtp_from = os.getenv("SMTP_FROM") or smtp_user

        if not smtp_host or not smtp_user or not smtp_pass:
            raise HTTPException(status_code=503, detail="SMTP no configurado. Configure SMTP_HOST, SMTP_USER y SMTP_PASS en variables de entorno.")

        # Construir mensaje
        msg = EmailMessage()
        msg["Subject"] = f"{tipo.capitalize()} {escritura}"
        msg["From"] = smtp_from
        msg["To"] = email_destino
        msg.set_content(f"Adjunto {tipo} {escritura}")

        # contenido puede venir como memoryview o bytes
        if isinstance(contenido, memoryview):
            contenido_bytes = contenido.tobytes()
        else:
            contenido_bytes = contenido

        msg.add_attachment(contenido_bytes, maintype="application", subtype="pdf", filename=nombre)

        # Enviar por SMTP
        try:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        finally:
            try:
                server.quit()
            except Exception:
                pass

        # Marcar como enviado en BD
        marcar_descarga_como_enviada(descarga_id)

        insert_log(
            "envio_descarga",
            f"Envío de {tipo} {escritura} a {email_destino}",
            "sistema"
        )

        return {
            "status": "ok",
            "mensaje": f"{tipo.capitalize()} enviado a {email_destino}",
            "email": email_destino,
            "archivo": nombre
        }

    except HTTPException:
        raise
    except Exception as e:
        insert_log("envio_descarga_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/descargas/pendientes")
def obtener_descargas_pendientes(tipo: str = None, limit: int = 100):
    """
    Obtiene descargas pendientes de envío.
    
    Args:
        tipo: 'recibo' o 'certificado' (opcional)
        limit: límite de resultados
    """
    try:
        resultado = obtener_descargas_pendientes_de_envio(tipo, limit)
        
        # Convertir archivos a base64
        descargas = []
        for desc in resultado.data:
            descarga_dict = desc.copy()
            if 'archivo_contenido' in descarga_dict:
                del descarga_dict['archivo_contenido']  # No enviar contenido binario en JSON
            descargas.append(descarga_dict)
        
        return {"status": "ok", "total": len(descargas), "descargas": descargas}
    
    except Exception as e:
        insert_log("obtener_pendientes_error", str(e), "sistema", "error")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/envios/devoluciones/start")
def start_devoluciones():
    """Encola la ejecución de `prueba_devolucion.py` como job en background."""
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

@app.post('/api/export/liq')
def export_liq_backup():
    """Genera un backup Excel de la tabla `liq` y devuelve el archivo generado."""
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
    
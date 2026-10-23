import base64
from datetime import datetime
from email.message import EmailMessage
from html import escape as html_escape
import os
import sys
import uuid
import threading
import subprocess
import io
import json
import logging
import warnings
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
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from database.supabase_client import delete_acta, get_acta_by_id, get_actas_by_escritura, get_pagos_2026_resumen, get_responsables, get_supabase, import_pagos_from_rows, insert_acta, insert_log, update_acta, upsert_pago_2026, upsert_responsable
from repositories.liq_repository import LiqRepository
from services.workflow_service import WorkflowService

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn").setLevel(logging.WARNING)
load_dotenv()

# Importaciones de módulos locales
try:
    from database.supabase_client import (
        check_table_exists,
        guardar_descarga,
        import_actas_from_rows,
        import_liq_from_rows,
        import_pagos_consolidado_from_rows,
    )
    from database.supabase_client import insert_liq_row, update_liq_row, get_pending_liq, update_liq_estado_by_escritura
    from database.supabase_client import update_liq_row_in_table
    from database.supabase_client import get_liq_stats
    from database.supabase_client import get_all_liq, get_processed_liq
    from database.supabase_client import move_liq_to_table, export_liq_to_excel
    from database.supabase_client import get_table_rows
except ImportError:
    from database.supabase_client import (
        check_table_exists,
        guardar_descarga,
        obtener_descargas_por_escritura,
        obtener_descargas_pendientes_de_envio,
        marcar_descarga_como_enviada,
        import_liq_from_rows,
        import_pagos_consolidado_from_rows,
    )
    from database.supabase_client import insert_liq_row, update_liq_row, get_pending_liq, update_liq_estado_by_escritura
    from database.supabase_client import update_liq_row_in_table
    from database.supabase_client import get_liq_stats
    from database.supabase_client import get_all_liq, get_processed_liq
    from database.supabase_client import move_liq_to_table, export_liq_to_excel
    from database.supabase_client import get_table_rows

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")
HTML_FILE = os.path.join(STATIC_DIR, "notaria_app.html")
SQL_SCHEMA_FILE = Path(APP_DIR, "supabase_schema_from_excel.sql")

app = FastAPI(title="Sistema Notarial")

CATALOGOS_REGISTRO_DEFAULT = {
    "gobernacion": ["antioquia", "atlantico", "bolivar", "boyaca", "cundinamarca", "magdalena", "meta", "santander", "tolima"],
    "pago": ["pendiente", "ingresado"],
    "notificacion": ["pendiente", "enviado"],
    "estado_ctl": ["pendiente", "procesando", "descargado", "enviado"],
}
TIPOS_CATALOGO_REGISTRO = set(CATALOGOS_REGISTRO_DEFAULT)
CATALOGOS_REGISTRO_LOCAL_FILE = Path(APP_DIR, "catalogos_registro.json")
CATALOGOS_REGISTRO_LOCK = threading.Lock()

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

LIQ_COLUMN_ALIASES = {
    'escritura': 'escritura',
    'nir': 'nir',
    'responsable': 'responsable',
    'gobernacion': 'gobernacion',
    'correo': 'correo',
    'notificacion': 'notificacion',
    'cert': 'cert',
    'pago': 'pago',
    'copias': 'copias',
    'fecha_liq': 'fecha_liq',
    'fecha_ben': 'benef',
    'actas': 'actas',
    'estado_acta': 'estado_acta',
    'recibo': 'recibo',
    'benef': 'benef',
    'vr_ben': 'vr_ben',
    'vr_beneficencia': 'vr_ben',
    'valor_beneficencia': 'vr_ben',
    'vr_reg': 'vr_reg',
    'vr_registro': 'vr_reg',
    'valor_registro': 'vr_reg',
    'liquidacion': 'liquidacion',
    'numero_liquidacion': 'liquidacion',
    'fecha_pago': 'fecha_pago',
    'fecha_proyectada_pago': 'fecha_proyectada_pago',
    'notificado': 'notificacion',
    'radicado': 'radicado',
    'ingreso': 'ingreso',
    'estado_ctl': 'estado_ctl',
    'observaciones': 'observaciones',
}

def _find_liq_header_row(raw: pd.DataFrame) -> int:
    """Encuentra el último bloque de encabezados del informe notarial."""
    candidates = []
    for index, row in raw.iterrows():
        values = {str(value).strip().lower() for value in row.tolist() if pd.notna(value)}
        if 'escritura' in values and 'notificacion' in values:
            candidates.append(index)
    if not candidates:
        raise ValueError(
            'No se encontró el encabezado del informe. Se esperaba una columna Escritura.'
        )
    return int(candidates[-1])

def _read_liq_file(contents: bytes, filename: str) -> pd.DataFrame:
    """Lee CSV/XLSX aunque el informe incluya metadata o encabezados repetidos."""
    lower_name = filename.lower()
    if lower_name.endswith(('.xlsx', '.xls', '.xlsm')):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                'ignore',
                message=r'(Unknown extension|Data Validation extension) is not supported.*',
                category=UserWarning,
                module=r'openpyxl\.worksheet\._reader',
            )
            workbook = pd.ExcelFile(io.BytesIO(contents))
            liq_sheet = next(
                (
                    sheet for sheet in workbook.sheet_names
                    if re.sub(r'[^a-z0-9]', '', sheet.casefold()) == 'liq'
                ),
                workbook.sheet_names[0],
            )
            raw = pd.read_excel(
                workbook, sheet_name=liq_sheet, dtype=str, header=None
            )
    else:
        text = contents.decode('utf-8-sig', errors='replace')
        first_line = text.splitlines()[0] if text.splitlines() else ''
        separator = ';' if first_line.count(';') >= first_line.count(',') else ','
        raw = pd.read_csv(
            io.StringIO(text), dtype=str, sep=separator, header=None,
            skip_blank_lines=False,
        )

    header_row = _find_liq_header_row(raw)
    headers = raw.iloc[header_row].fillna('unnamed').astype(str).str.strip().str.lower()
    headers = (
        headers.str.replace('á', 'a')
        .str.replace('é', 'e')
        .str.replace('í', 'i')
        .str.replace('ó', 'o')
        .str.replace('ú', 'u')
        .str.replace('ñ', 'n')
        .str.replace(r'[\s-]+', '_', regex=True)
    )
    data = raw.iloc[header_row + 1:].copy()
    data.columns = headers.tolist()
    data = data.loc[:, ~data.columns.str.startswith('unnamed')]
    data = data.rename(columns={key: value for key, value in LIQ_COLUMN_ALIASES.items()})
    return data

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

@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job_id no existe")
    return job

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

@app.get("/api/dashboard/status")
def dashboard_status():
    """Retorna un resumen del estado del sistema y tareas para el Dashboard."""
    try:
        # Reutilizamos las estadísticas de liquidaciones que ya tienes programadas
        stats = get_liq_stats()
    except Exception:
        stats = {"total": 0, "liq": 0, "liq_2025": 0, "liq_2026": 0}
        
    # Contamos de forma rápida cuántas tareas en background están activas
    active_jobs = sum(1 for j in JOBS.values() if j.get("status") in ["running", "queued"])

    return {
        "status": "online",
        "database_connected": get_supabase() is not None,
        "active_background_jobs": active_jobs,
        "stats": stats
    }

@app.post("/api/actas/import")
async def import_actas_file(file: UploadFile = File(...), anio: int = 2026):
    """
    Importa actas de depósito desde el CSV/Excel crudo que exporta el sistema
    (ej. 'ConsultaRegistrosActasDeposito'). Reproduce en Python lo que hacía la macro
    'Depurar' de Excel:
      - Descarta filas con Tipo Deposito = 'Derechos Notariales' (no aplica a Vr_Ben/Vr_Reg)
      - Descarta filas sin escritura asignada todavía (Escritura = '---')
      - Extrae el número de escritura de formatos como '1244 de 2026'
      - Convierte 'Fecha Acta' (ej. '24/Jul/2026 11:16 am') a fecha ISO
    No hace falta correr la macro antes de subir el archivo.
    """
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")

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
            """Convierte NaN/None a None de forma segura; strip() a lo demás.
            Necesario porque df.where(pd.notna(df), None) no siempre reemplaza los NaN
            cuando la columna quedó con dtype 'str' de pandas, dejando floats NaN sueltos."""
            if v is None:
                return None
            if isinstance(v, float) and pd.isna(v):
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
                '%d/%m/%y', '%d/%m/%Y',
                '%d-%m-%y', '%d-%m-%Y',
                '%d/%b/%Y', '%d/%B/%Y',
                '%d-%b-%Y', '%d-%B-%Y',
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
                'estado': limpiar(row.get('estado')) or 'activa',
            })

        if not filas:
            raise HTTPException(
                status_code=400,
                detail="No se encontraron filas válidas para importar. Revisa que el archivo tenga columnas "
                       "'Tipo Deposito' (Beneficencia / Registro Instrumentos Publicos) y 'Escritura' asignada."
            )

        result = import_actas_from_rows(filas, batch_size=200)
        result['omitidas_derechos_notariales'] = omitidas_tipo
        result['omitidas_sin_escritura'] = omitidas_sin_escritura

        try:
            insert_log("import_actas", f"Archivo {file.filename}: {result['nuevos']} nuevos, "
                                        f"{omitidas_tipo} omitidas por tipo, {omitidas_sin_escritura} sin escritura", "sistema")
        except Exception:
            pass

        return {"status": "ok", "import_result": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pagos2026")
def pagos2026_resumen():
    """Lista las escrituras que tienen actas registradas, con el match Vr_Ben+Vr_Reg vs. lo pagado."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        return get_pagos_2026_resumen()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pagos2026/{escritura}/actas")
def pagos2026_actas_detalle(escritura: int):
    """Devuelve las actas individuales de una escritura (para la vista de detalle)."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
        return get_actas_by_escritura(escritura)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/pagos2026/{escritura}/detalle")
def pagos2026_detalle_unificado(escritura: int):
    try:
        db = get_supabase()
        if not db:
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        response = db.table("pagos_2026").select("*").eq("escritura", escritura).limit(1).execute()
        pago = (response.data or [{}])[0]
        return {"pago": pago, "actas": get_actas_by_escritura(escritura)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@app.patch("/api/pagos2026/{escritura}")
def actualizar_pago2026(escritura: int, body: dict):
    """Actualiza los valores manuales de una escritura: Vr_Ben, Vr_Reg y fecha proyectada de pago."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")

        campos_pago = ('vr_ben', 'vr_reg', 'fecha_pago', 'observaciones', 'liquidacion', 'responsable')
        cambios = {campo: body[campo] for campo in campos_pago if campo in body}
        result = upsert_pago_2026(escritura, **cambios)
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
    """Registra una nueva acta. La escritura queda automáticamente asignada al módulo Pagos 2026."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
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
    """Devuelve una acta por su id."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
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
    """Actualiza una acta registrada por su id."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")
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
    """Elimina una acta por su id."""
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env")

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
def generar_informe_pagos(fecha_pago: Optional[str] = None, gobernacion: Optional[str] = None):
    try:
        db = get_supabase()
        if not db:
            raise HTTPException(status_code=503, detail="Supabase no configurado.")

        fecha_filtro = None
        fecha_mostrar = datetime.now().strftime('%d/%m/%Y')  # Por defecto la fecha actual de Colombia

        if fecha_pago:
            fecha_pago = fecha_pago.strip()
            if "/" in fecha_pago:
                partes = fecha_pago.split("/")
                if len(partes) == 3:
                    # Si viene como DD/MM/YYYY
                    if len(partes[0]) == 2 and len(partes[2]) == 4:
                        fecha_filtro = f"{partes[2]}-{partes[1]}-{partes[0]}"  # YYYY-MM-DD para BD
                        fecha_mostrar = f"{partes[0]}/{partes[1]}/{partes[2]}"  # DD/MM/YYYY para pantalla
                    # Si viene como YYYY/MM/DD
                    elif len(partes[0]) == 4:
                        fecha_filtro = f"{partes[0]}-{partes[1]}-{partes[2]}"
                        fecha_mostrar = f"{partes[2]}/{partes[1]}/{partes[0]}"
            elif "-" in fecha_pago:
                partes = fecha_pago.split("-")
                if len(partes) == 3:
                    # Si viene como YYYY-MM-DD
                    if len(partes[0]) == 4:
                        fecha_filtro = fecha_pago
                        fecha_mostrar = f"{partes[2]}/{partes[1]}/{partes[0]}"
                    # Si viene como DD-MM-YYYY
                    elif len(partes[2]) == 4:
                        fecha_filtro = f"{partes[2]}-{partes[1]}-{partes[0]}"
                        fecha_mostrar = f"{partes[0]}/{partes[1]}/{partes[2]}"

        # 2. Consultar actas ordenadas por ID y construir mapa con fallback de campos
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

            # Extraer y limpiar datos
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
                # Fallback: completar datos que venían vacíos
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

            # Acumular monto total de actas
            val_acta = float(a.get('valor_acta') or 0)
            mapa_actas[esc_k]["pago_cli"] += val_acta

            # Agregar número de acta sin duplicar
            acta_num = str(a.get("acta") or "").strip()
            if acta_num and acta_num not in mapa_actas[esc_k]["actas"]:
                mapa_actas[esc_k]["actas"].append(acta_num)

        # 3. Consultar pagos_2026 con filtro opcional de fecha
        query = db.table("pagos_2026").select("*")
        if fecha_filtro:
            query = query.eq("fecha_pago", fecha_filtro)
            
        pagos_resp = query.execute()
        registros_pagos = pagos_resp.data or []

        # 4. ORDENAR LOS REGISTROS DE MENOR A MAYOR POR NÚMERO DE ESCRITURA
        def clave_orden_escritura(item):
            esc_str = str(item.get("escritura", "")).strip()
            solo_numeros = ''.join(c for c in esc_str if c.isdigit())
            return int(solo_numeros) if solo_numeros else 0

        registros_pagos.sort(key=clave_orden_escritura)

        # 5. Consultar tabla liq para NIR y datos adicionales
        result_liq = db.table('liq').select('escritura, nir, responsable').execute()

        mapa_liq = {}
        if result_liq.data:
            for item in result_liq.data:
                esc_k = str(normalizar_escritura(item.get("escritura"))).strip()
                if esc_k:
                    mapa_liq[esc_k] = item

        result_liq_2026 = db.table('liq').select('escritura, gobernacion').execute()
        mapa_gobernaciones = {}
        if result_liq_2026.data:
            for item in result_liq_2026.data:
                esc_k = str(normalizar_escritura(item.get("escritura"))).strip()
                if esc_k:
                    mapa_gobernaciones[esc_k] = item.get("gobernacion") or ""

        gobernacion_filtro = (gobernacion or "todas").strip().casefold()

        # Acumuladores de totales
        tot_ben = 0.0
        tot_reg = 0.0
        tot_recibo = 0.0
        tot_pago_cli = 0.0
        tot_faltante = 0.0
        tot_sobrante = 0.0

        secciones_gobernacion = {}

        # 6. Generar filas HTML ya ordenadas
        for item in registros_pagos:
            esc_raw = item.get("escritura")
            esc = str(normalizar_escritura(esc_raw)).strip()
            gob_raw = item.get("gobernacion") or mapa_gobernaciones.get(esc) or ""
            gob_clave = str(gob_raw).strip().casefold()
            if gobernacion_filtro not in ("", "todas") and gob_clave != gobernacion_filtro:
                continue

            gob_nombre = str(gob_raw).strip().title() if gob_clave else "Sin gobernación"
            seccion = secciones_gobernacion.setdefault(gob_clave, {
                "nombre": gob_nombre,
                "filas": [],
                "tot_ben": 0.0,
                "tot_reg": 0.0,
                "tot_recibo": 0.0,
                "tot_pago_cli": 0.0,
                "tot_faltante": 0.0,
                "tot_sobrante": 0.0,
            })

            datos_acta = mapa_actas.get(esc, {})
            datos_liq = mapa_liq.get(esc, {})

            # Responsable, Liquidación y NIR
            resp = item.get("responsable") or datos_liq.get("responsable") or "Venta"
            liq_val = str(item.get("liquidacion") or "").strip()
            nir_val = str(datos_liq.get("nir") or "").strip()        

            # Cadena de números de actas
            actas_list = datos_acta.get("actas", [])
            actas_str = "-".join(actas_list) if actas_list else str(item.get("actas") or "")

            # Contribuyente y Cédula/NIT
            contrib = str(datos_acta.get("cliente") or "").strip()
            doc_raw = str(datos_acta.get("documento") or item.get("documento") or "").strip()
            identif = re.sub(r'[^0-9]', '', doc_raw) if ("C.C." in doc_raw or "N.I.T." in doc_raw) else doc_raw

            # Montos
            vr_ben = float(item.get("vr_ben") or 0)
            vr_reg = float(item.get("vr_reg") or 0)
            total_recibo = vr_ben + vr_reg

            # PAGO CLIENTE = Suma total de las actas de esta escritura
            pago_cli = datos_acta.get("pago_cli", 0.0)

            # Faltante / Sobrante
            faltante = max(total_recibo - pago_cli, 0.0)
            sobrante = max(pago_cli - total_recibo, 0.0)

            # Acumular globales
            tot_ben += vr_ben
            tot_reg += vr_reg
            tot_recibo += total_recibo
            tot_pago_cli += pago_cli
            tot_faltante += faltante
            tot_sobrante += sobrante

            seccion["tot_ben"] += vr_ben
            seccion["tot_reg"] += vr_reg
            seccion["tot_recibo"] += total_recibo
            seccion["tot_pago_cli"] += pago_cli
            seccion["tot_faltante"] += faltante
            seccion["tot_sobrante"] += sobrante

            seccion["filas"].append(f"""
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
            """)

        filas_html = ""
        for gob_clave in sorted(secciones_gobernacion, key=lambda clave: (clave == "", clave)):
            seccion = secciones_gobernacion[gob_clave]
            filas_html += f"""
            <tr class="group-header"><td colspan="13">Gobernación: {seccion['nombre']}</td></tr>
            {''.join(seccion['filas'])}
            <tr class="group-total">
                <td colspan="5">Subtotal {seccion['nombre']}</td>
                <td class="right">$ {seccion['tot_ben']:,.0f}</td>
                <td class="right">$ {seccion['tot_reg']:,.0f}</td>
                <td class="right">$ {seccion['tot_recibo']:,.0f}</td>
                <td class="right">$ {seccion['tot_pago_cli']:,.0f}</td>
                <td class="right text-red">$ {seccion['tot_faltante']:,.0f}</td>
                <td class="right text-green">$ {seccion['tot_sobrante']:,.0f}</td>
                <td colspan="2"></td>
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
                .kpi-table {{ width: 100%; border-collapse: collapse; margin-bottom: 8px; }}
                .kpi-table td {{ border: 1px solid #000; padding: 6px 4px; text-align: center; font-weight: bold; font-size: 12px; }}
                .kpi-date {{ background-color: #e5e7eb; font-size: 13px; }}
                .kpi-green {{ background-color: #dcfce7; font-size: 13px; }}
                .kpi-title {{ font-size: 22px; font-style: italic; font-weight: 900; letter-spacing: 2px; text-transform: uppercase; }}
                .main-table {{ width: 100%; border-collapse: collapse; font-size: 10.5px; }}
                .main-table th {{ background-color: #e5e7eb; color: #000; border: 1px solid #000; padding: 5px 3px; font-weight: bold; text-align: center; white-space: nowrap; }}
                .main-table td {{ border: 1px solid #71717a; padding: 4px 5px; white-space: nowrap; }}
                .main-table tfoot td {{ background-color: #f3f4f6; border: 1px solid #000; border-top: 2px solid #000; border-bottom: 3px double #000; font-weight: bold; padding: 6px 5px; }}
                .group-header td {{ background-color: #dbeafe; font-weight: bold; text-align: left; padding: 7px 5px; }}
                .group-total td {{ background-color: #f3f4f6; border-top: 2px solid #71717a; font-weight: bold; }}
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
                    <img src="/static/logo.jpeg" alt="Logo Notaría" />
                </div>
                <div  style="flex: 2; text-align: center; margin: 0;">
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
    
def normalizar_escritura(esc):
    """Limpia la escritura removiendo caracteres no numéricos y ceros a la izquierda."""
    if esc is None:
        return ""
    val = re.sub(r'\D', '', str(esc))
    return val.lstrip('0') if val else ""

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
        if str(body.get('pago') or '').strip().casefold() == 'ingresado':
            retirar_actas_por_escritura(escritura)
        return {"status": "ok", "updated": result.data}
    except Exception as e:
        insert_log("update_liq", str(e), "sistema", "error")
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
        allowed = {'liq', 'liquida', 'liq_2025', 'liq_2026', 'pagos_2026', 'pagos_consolidado'}
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

@app.get('/api/ventanilla')
def obtener_ventanilla(limit: int = 10000):
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail='Supabase no configurado.')
        escrituras_con_actas = set(get_escrituras_con_actas_activas())
        por_escritura = {}
        for table_name in ('liq_2025', 'liq'):
            result = get_table_rows(table_name, limit=min(max(limit, 1), 10000), page=1, sort_by='updated_at', desc=True)
            rows = result.data if hasattr(result, 'data') else result
            for row in rows or []:
                escritura = normalizar_escritura(row.get('escritura') or row.get('escritura_str'))
                if not escritura or escritura in escrituras_con_actas or escritura in por_escritura:
                    continue
                por_escritura[escritura] = {**row, 'escritura': row.get('escritura') or row.get('escritura_str'), 'tabla_origen': table_name}
        return list(por_escritura.values())
    except HTTPException:
        raise
    except Exception as e:
        insert_log('consulta_ventanilla', str(e), 'sistema', 'error')
        raise HTTPException(status_code=500, detail=str(e))

def _filas_para_informe(hoja: str) -> list[dict]:
    if not get_supabase():
        raise HTTPException(status_code=503, detail='Supabase no configurado.')
    if hoja == 'ventanilla':
        return obtener_ventanilla()
    if hoja == 'pagos2026':
        filas = get_pagos_2026_resumen()
        respuesta_liq = get_supabase().table('liq').select('escritura, gobernacion, estado_ctl, responsable').execute()
        mapa_liq = {normalizar_escritura(row.get('escritura')): row for row in (respuesta_liq.data or [])}
        return [{**row, **{key: value for key, value in mapa_liq.get(normalizar_escritura(row.get('escritura')), {}).items() if key not in row}} for row in filas]
    if hoja == 'liq_2026':
        result = get_table_rows('liq_2026', limit=10000, page=1, sort_by='updated_at', desc=True)
        return result.data if hasattr(result, 'data') else result or []
    if hoja in {'registros', 'pendientes', 'procesar'}:
        result = get_table_rows('liq', limit=10000, page=1, sort_by='updated_at', desc=True)
        filas = result.data if hasattr(result, 'data') else result or []
        activas = set(get_escrituras_con_actas_activas())
        if hoja == 'registros':
            return [row for row in filas if normalizar_escritura(row.get('escritura')) in activas]
        filas.extend(obtener_ventanilla())
        if hoja == 'pendientes':
            return [row for row in filas if str(row.get('notificacion') or '').strip().casefold() == 'enviado' and str(row.get('pago') or '').strip().casefold() == 'pendiente' and str(row.get('estado_ctl') or '').strip().casefold() == 'pendiente']
        return [row for row in filas if str(row.get('notificacion') or '').strip().casefold() == 'enviado' and str(row.get('pago') or '').strip().casefold() == 'ingresado' and str(row.get('estado_ctl') or '').strip().casefold() == 'pendiente']
    raise HTTPException(status_code=400, detail='Hoja de informe no reconocida.')

@app.get('/api/informes/opciones')
def opciones_informe(hoja: str):
    filas = _filas_para_informe(hoja)
    def valores(campo):
        return sorted({str(row.get(campo) or '').strip() for row in filas if str(row.get(campo) or '').strip()}, key=str.casefold)
    estados = sorted({str(row.get('estado_ctl') or row.get('estado') or row.get('pago') or '').strip() for row in filas if str(row.get('estado_ctl') or row.get('estado') or row.get('pago') or '').strip()}, key=str.casefold)
    return {'responsables': valores('responsable'), 'gobernaciones': valores('gobernacion'), 'estados': estados}

@app.get('/api/informes/tabla', response_class=HTMLResponse)
def generar_informe_tabla(hoja: str, responsable: Optional[str] = None, gobernacion: Optional[str] = None, estado: Optional[str] = None):
    filas = _filas_para_informe(hoja)
    filtros = {'responsable': responsable, 'gobernacion': gobernacion, 'estado': estado}
    for campo, valor in filtros.items():
        if valor:
            key = 'estado_ctl' if campo == 'estado' else campo
            filas = [row for row in filas if str(row.get(key) or row.get('estado') or row.get('pago') or '').strip().casefold() == valor.strip().casefold()]

    columnas_por_hoja = {
        'pagos2026': ['escritura', 'responsable', 'gobernacion', 'liquidacion', 'vr_ben', 'vr_reg', 'total', 'pago_cli', 'faltante', 'sobrante', 'fecha_pago', 'estado_ctl'],
    }
    columnas = columnas_por_hoja.get(hoja, ['escritura', 'nir', 'correo', 'responsable', 'gobernacion', 'notificacion', 'pago', 'estado_ctl', 'fecha_proceso', 'observaciones'])
    titulo = {'registros': 'Registros', 'pendientes': 'Pendientes', 'procesar': 'Procesar', 'ventanilla': 'Ventanilla', 'liq_2026': 'liq_2026', 'pagos2026': 'Pagos 2026'}.get(hoja, hoja)
    encabezados = ''.join(f'<th>{html_escape(columna.replace("_", " ").title())}</th>' for columna in columnas)
    cuerpo = ''.join('<tr>' + ''.join(f'<td>{html_escape(str(row.get(columna) if row.get(columna) is not None else ""))}</td>' for columna in columnas) + '</tr>' for row in filas)
    resumen_filtros = ' · '.join(f'{html_escape(etiqueta)}: {html_escape(valor)}' for etiqueta, valor in [('Responsable', responsable), ('Gobernación', gobernacion), ('Estado', estado)] if valor) or 'Sin filtros'
    html = f'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Informe {html_escape(titulo)}</title>
    <style>body{{font:14px Arial,sans-serif;color:#17212b;margin:24px}}header{{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:18px}}h1{{font-size:22px;margin:0 0 6px}}.muted{{color:#5d6873}}button{{border:0;background:#1f5d54;color:#fff;padding:9px 14px;border-radius:4px;cursor:pointer}}table{{width:100%;border-collapse:collapse}}th,td{{border:1px solid #9aa3aa;padding:6px 8px;text-align:left}}th{{background:#e9eef0}}tbody tr:nth-child(even){{background:#f6f8f8}}@media print{{button{{display:none}}body{{margin:0}}}}</style></head><body>
    <header><div><h1>Informe {html_escape(titulo)}</h1><div class="muted">{resumen_filtros}</div><div class="muted">{len(filas)} registros · Generado {datetime.now().strftime('%d/%m/%Y %H:%M')}</div></div><button onclick="window.print()">Imprimir</button></header>
    <table><thead><tr>{encabezados}</tr></thead><tbody>{cuerpo or f'<tr><td colspan="{len(columnas)}">No hay registros con estos filtros.</td></tr>'}</tbody></table></body></html>'''
    return HTMLResponse(content=html)

@app.patch('/api/liq/table/{table_name}/{escritura}')
def patch_liq_table_row(table_name: str, escritura: str, body: dict):
    if table_name not in {'liq', 'liq_2025'}:
        raise HTTPException(status_code=400, detail='La tabla indicada no pertenece a Ventanilla.')
    allowed_fields = {'notificacion', 'pago', 'estado_ctl', 'gobernacion', 'nir', 'correo', 'responsable', 'observaciones'}
    changes = {key: value for key, value in body.items() if key in allowed_fields}
    if not changes:
        raise HTTPException(status_code=400, detail='No hay campos válidos para actualizar.')
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail='Supabase no configurado.')
        if table_name == 'liq':
            result = update_liq_row(escritura, changes)
            if str(changes.get('pago') or '').strip().casefold() == 'ingresado':
                retirar_actas_por_escritura(escritura)
        else:
            result = update_liq_row_in_table(table_name, escritura, changes)
        return {'status': 'ok', 'updated': result.data if hasattr(result, 'data') else result}
    except HTTPException:
        raise
    except Exception as e:
        insert_log('update_ventanilla', str(e), 'sistema', 'error')
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/responsables')
def get_responsables_api():
    try:
        return get_responsables()
    except Exception as e:
        insert_log('consulta_responsables', str(e), 'sistema', 'error')
        return []

@app.get('/api/catalogos-registro')
def get_catalogos_registro():
    catalogos = {tipo: list(valores) for tipo, valores in CATALOGOS_REGISTRO_DEFAULT.items()}
    try:
        if CATALOGOS_REGISTRO_LOCAL_FILE.exists():
            local_catalogos = json.loads(
                CATALOGOS_REGISTRO_LOCAL_FILE.read_text(encoding='utf-8')
            )
            for tipo, valores in local_catalogos.items():
                if tipo in TIPOS_CATALOGO_REGISTRO and isinstance(valores, list):
                    catalogos[tipo] = list(dict.fromkeys([
                        *catalogos[tipo],
                        *(str(valor).strip().lower() for valor in valores if str(valor).strip()),
                    ]))
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as e:
        logging.exception("No se pudo leer el catálogo local de registros")
        raise HTTPException(
            status_code=503,
            detail=f'No se pudo leer el catálogo local de registros: {e}',
        )

    db = get_supabase()
    if not db:
        return catalogos
    try:
        response = db.table('catalogos_registro').select('tipo, valor').order('valor').execute()
        for row in response.data or []:
            tipo = row.get('tipo')
            valor = str(row.get('valor') or '').strip()
            if tipo in TIPOS_CATALOGO_REGISTRO and valor:
                if valor not in catalogos[tipo]:
                    catalogos[tipo].append(valor)
        return catalogos
    except Exception as e:
        logging.warning("Catálogos Supabase no disponibles; se usa el respaldo local: %s", e)
        return catalogos

@app.post('/api/catalogos-registro')
def guardar_catalogo_registro(payload: dict):
    tipo = str(payload.get('tipo') or '').strip()
    valor = str(payload.get('valor') or '').strip().lower()
    if tipo not in TIPOS_CATALOGO_REGISTRO or not valor:
        raise HTTPException(status_code=400, detail='Tipo o valor de catálogo inválido.')
    db = get_supabase()
    try:
        if db:
            db.table('catalogos_registro').upsert(
                {'tipo': tipo, 'clave': valor, 'valor': valor},
                on_conflict='tipo,clave',
            ).execute()
            return {'status': 'ok', 'tipo': tipo, 'valor': valor, 'storage': 'supabase'}
    except Exception as db_error:
        logging.warning(
            "No se pudo guardar el catálogo en Supabase; se usará el respaldo local: %s",
            db_error,
        )

    try:
        with CATALOGOS_REGISTRO_LOCK:
            local_catalogos = {key: list(values) for key, values in CATALOGOS_REGISTRO_DEFAULT.items()}
            if CATALOGOS_REGISTRO_LOCAL_FILE.exists():
                stored_catalogos = json.loads(
                    CATALOGOS_REGISTRO_LOCAL_FILE.read_text(encoding='utf-8')
                )
                for key, values in stored_catalogos.items():
                    if key in TIPOS_CATALOGO_REGISTRO and isinstance(values, list):
                        local_catalogos[key] = list(dict.fromkeys([
                            *local_catalogos[key],
                            *(str(item).strip().lower() for item in values if str(item).strip()),
                        ]))
            local_catalogos[tipo] = list(dict.fromkeys([*local_catalogos[tipo], valor]))
            temporary_file = CATALOGOS_REGISTRO_LOCAL_FILE.with_suffix('.tmp')
            temporary_file.write_text(
                json.dumps(local_catalogos, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
            temporary_file.replace(CATALOGOS_REGISTRO_LOCAL_FILE)
        logging.warning(
            "Catálogo guardado en el respaldo local (%s); aplique el esquema Supabase para sincronizarlo entre instancias.",
            CATALOGOS_REGISTRO_LOCAL_FILE,
        )
        return {'status': 'ok', 'tipo': tipo, 'valor': valor, 'storage': 'local'}
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as e:
        logging.exception("No se pudo guardar el catálogo local de registros")
        raise HTTPException(
            status_code=503,
            detail=f'No se pudo guardar el catálogo ni en Supabase ni en el respaldo local: {e}',
        )

@app.post('/api/responsables')
def create_responsable(payload: dict):
    try:
        nombre = str(payload.get('nombre') or payload.get('responsable') or '').strip()
        if not nombre:
            raise HTTPException(status_code=400, detail='Falta el nombre del responsable')
        return {"status": "ok", "responsables": upsert_responsable(nombre)}
    except HTTPException:
        raise
    except Exception as e:
        insert_log('guardar_responsable', str(e), 'sistema', 'error')
        raise HTTPException(status_code=500, detail=str(e))

@app.get('/api/gobernaciones')
def get_gobernaciones_api():
    try:
        return get_gobernaciones()
    except Exception as e:
        insert_log('consulta_gobernaciones', str(e), 'sistema', 'error')
        return []

@app.post('/api/gobernaciones')
def create_gobernacion(payload: dict):
    try:
        nombre = str(payload.get('nombre') or payload.get('gobernacion') or '').strip()
        if not nombre:
            raise HTTPException(status_code=400, detail='Falta el nombre de la gobernación')
        return {"status": "ok", "gobernaciones": upsert_gobernacion(nombre)}
    except HTTPException:
        raise
    except Exception as e:
        insert_log('guardar_gobernacion', str(e), 'sistema', 'error')
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

@app.get('/api/actas/activas/escrituras')
def escrituras_con_actas_activas():
    try:
        if not get_supabase():
            raise HTTPException(status_code=503, detail="Supabase no configurado.")
        return get_escrituras_con_actas_activas()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
                'estado': limpiar(row.get('estado')) or 'activa',
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
    """Recibe un payload JSON para enviar un único recibo."""
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

@app.post("/api/liq/import")
async def import_liq_file(file: UploadFile = File(...), anio: int = 2026):
    """
    Importa el informe de liquidaciones (Informe_Not_38_Liq.csv / .xlsx).

    Estructura del archivo:
      Fila 1 → metadata de la notaría  (se descarta)
      Fila 2 → encabezado bloque "Sin Actas" (se descarta)
      Fila 3 → única fila del bloque "Sin Actas" (se descarta)
      Fila 4 → encabezados reales:
                Escritura;Nir;Responsable;Gobernacion;correo;Notificacion;
                Cert;Pago;Copias;Fecha_liq;Benef;Fecha_Pago;Radicado;Ingreso;
                Estado_Ctl;Observaciones
      Fila 5+ → datos

    Limpieza aplicada:
      - Fechas "00-01-00" (centinela de fecha vacía) → None
      - Columnas extras vacías al final → ignoradas
      - Valores vacíos / NaN → None
    """
    try:
        if not get_supabase():
            raise HTTPException(
                status_code=503,
                detail="Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_KEY en .env"
            )

        contents = await file.read()
        # Lee el bloque de datos real sin depender de una fila fija.
        df = _read_liq_file(contents, file.filename or '')

        # ── Helpers ───────────────────────────────────────────────────────────
        def limpiar(v):
            if v is None:
                return None
            if isinstance(v, float) and pd.isna(v):
                return None
            s = str(v).strip()
            return s or None

        MESES = {
            'ene': 1, 'jan': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'apr': 4, 'may': 5, 'jun': 6,
            'jul': 7, 'ago': 8, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12, 'dec': 12,
        }

        FECHA_CENTINELA = {'00-01-00', '00/01/00', '0/1/0', ''}

        def parsear_fecha(raw):
            if isinstance(raw, (datetime, pd.Timestamp)):
                return raw.date().isoformat()

            raw = limpiar(raw)
            if not raw or raw in FECHA_CENTINELA:
                return None
            if re.fullmatch(r'\d{1,2}:\d{2}:\d{2}', raw):
                return None

            try:
                return datetime.fromisoformat(raw.replace('Z', '+00:00')).date().isoformat()
            except ValueError:
                pass

            # Formato dominante en este CSV: DD-MM-YY  (ej. 16-08-26)
            formats = [
                '%d-%m-%y', '%d-%m-%Y',
                '%d/%m/%y', '%d/%m/%Y',
                '%d/%b/%Y', '%d/%B/%Y',
                '%d-%b-%Y', '%d-%B-%Y',
                '%Y-%m-%d', '%Y/%m/%d',
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(raw, fmt).date().isoformat()
                except ValueError:
                    continue

            # Fallback: DD/Mes/YYYY con mes en texto
            m = re.match(r'(\d{1,2})/([A-Za-z]{3})/(\d{4})', raw)
            if m:
                dia, mes_txt, anio_txt = m.groups()
                mes = MESES.get(mes_txt.lower())
                if mes:
                    try:
                        return datetime(int(anio_txt), mes, int(dia)).date().isoformat()
                    except ValueError:
                        pass
            return None

        def parsear_valor(raw):
            raw = limpiar(raw)
            if not raw:
                return None
            value = re.sub(r'[^0-9,.-]', '', raw)
            if not value:
                return None
            if ',' in value and '.' in value:
                if value.rfind(',') > value.rfind('.'):
                    value = value.replace('.', '').replace(',', '.')
                else:
                    value = value.replace(',', '')
            elif ',' in value:
                tail = value.rsplit(',', 1)[1]
                value = value.replace(',', '.') if len(tail) in (1, 2) else value.replace(',', '')
            elif '.' in value:
                tail = value.rsplit('.', 1)[1]
                if len(tail) == 3:
                    value = value.replace('.', '')
            try:
                amount = float(value)
                return int(amount) if amount.is_integer() else amount
            except ValueError:
                return None

        # ── Construcción de filas ─────────────────────────────────────────────
        omitidas_sin_escritura = 0
        filas = []
        pagos_importados = []

        for row in df.to_dict(orient='records'):
            esc_raw = limpiar(row.get('escritura'))

            # Descartar filas de encabezado repetido o sin número de escritura
            if not esc_raw or not esc_raw.isdigit():
                omitidas_sin_escritura += 1
                continue

            fecha_benef = parsear_fecha(row.get('benef'))
            fecha_pago_archivo = parsear_fecha(row.get('fecha_pago'))
            fecha_proyectada = parsear_fecha(row.get('fecha_proyectada_pago')) or fecha_benef
            vr_ben = parsear_valor(row.get('vr_ben'))
            vr_reg = parsear_valor(row.get('vr_reg'))
            liquidacion = limpiar(row.get('liquidacion'))

            filas.append({
                'escritura':    int(esc_raw),
                'nir':          limpiar(row.get('nir')),
                'responsable':  limpiar(row.get('responsable')),
                'gobernacion':  limpiar(row.get('gobernacion')),
                'correo':       limpiar(row.get('correo')),
                'copias':       limpiar(row.get('copias')),
                'fecha_liq':    parsear_fecha(row.get('fecha_liq')),
                'benef':        fecha_benef,
                'fecha_pago':   fecha_pago_archivo,
                'radicado':     limpiar(row.get('radicado')),
                'cert':         limpiar(row.get('cert')),
                'pago':         limpiar(row.get('pago')),
                'ingreso':      limpiar(row.get('ingreso')),
                'estado_ctl':   limpiar(row.get('estado_ctl')),
                'observaciones':limpiar(row.get('observaciones')),
            })
            pago = {'escritura': int(esc_raw)}
            if vr_ben is not None:
                pago['vr_ben'] = vr_ben
            if vr_reg is not None:
                pago['vr_reg'] = vr_reg
            if liquidacion is not None:
                pago['liquidacion'] = liquidacion
            if fecha_proyectada is not None:
                pago['fecha_pago'] = fecha_proyectada
            if len(pago) > 1:
                pagos_importados.append(pago)

        if not filas:
            raise HTTPException(
                status_code=400,
                detail=(
                    "No se encontraron filas válidas. Verifica que el archivo sea el informe "
                    "de liquidaciones con encabezados en la fila 4 "
                    "(Escritura; Nir; Responsable; Gobernacion; ...)."
                )
            )

        # ── Upsert en Supabase ────────────────────────────────────────────────
        # Ajusta la función/tabla según tu proyecto
        result = import_liq_from_rows(filas, batch_size=200, payment_rows=pagos_importados)
        result['omitidas_sin_escritura'] = omitidas_sin_escritura

        try:
            insert_log(
                "import_liq",
                f"Archivo {file.filename}: {result.get('nuevos', '?')} nuevos, "
                f"{result.get('pagos_actualizados', 0)} pagos asociados actualizados, "
                f"{omitidas_sin_escritura} filas omitidas (sin escritura/encabezado)",
                "sistema"
            )
        except Exception:
            pass

        return {"status": "ok", "import_result": result}

    except HTTPException:
        raise
    except Exception as e:
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

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False, access_log=False)
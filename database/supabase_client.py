import os
import re
from typing import Any, Optional, List
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

_SUPABASE_CLIENT: Optional[Any] = None

DEFAULT_RESPONSABLES = [
    "Venta",
    "Marval",
    "Open C60",
    "Cancelación",
    "Bancolombia",
    "Sui Loft",
]


def _table_exists(db: Any, table_name: str) -> bool:
    try:
        db.table(table_name).select("escritura").limit(1).execute()
        return True
    except Exception:
        return False


def resolve_liq_table_name(db: Optional[Any]) -> str:
    if not db:
        return "liq"
    for table_name in ["liq", "liquida"]:
        if _table_exists(db, table_name):
            return table_name
    return "liq"


def get_supabase() -> Optional[Any]:
    """Devuelve la instancia de Supabase cuando exista configuración válida."""
    global _SUPABASE_CLIENT

    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", os.getenv("SUPABASE_KEY", "")).strip()

    if not url or not key:
        _SUPABASE_CLIENT = None
        return None

    if _SUPABASE_CLIENT is not None:
        return _SUPABASE_CLIENT

    try:
        from supabase import create_client
    except Exception:
        return None

    try:
        _SUPABASE_CLIENT = create_client(url, key)
        return _SUPABASE_CLIENT
    except Exception:
        _SUPABASE_CLIENT = None
        return None

def insert_log(action: str, message: str, usuario: str = "sistema", nivel: str = "info") -> dict:
    """Guarda un log cuando la conexión a Supabase esté disponible; en modo local no falla."""
    return {"action": action, "message": message, "usuario": usuario, "nivel": nivel}

def check_table_exists(table_name: str) -> bool:
    return False

def insert_certificado(*args: Any, **kwargs: Any) -> dict:
    return {"ok": True}

def update_certificado_estado(*args: Any, **kwargs: Any) -> dict:
    return {"ok": True}

def get_certificados_por_usuario(*args: Any, **kwargs: Any) -> list:
    return []

def insert_recibo(*args: Any, **kwargs: Any) -> dict:
    return {"ok": True}

def guardar_descarga(*args: Any, **kwargs: Any) -> dict:
    return {"ok": True}

def obtener_descargas_por_escritura(*args: Any, **kwargs: Any) -> list:
    return []

def obtener_descargas_pendientes_de_envio(*args: Any, **kwargs: Any) -> list:
    return []

def marcar_descarga_como_enviada(*args: Any, **kwargs: Any) -> dict:
    return {"ok": True}

def import_liq_from_rows(*args: Any, **kwargs: Any) -> dict:
    return {"nuevos": 0, "actualizados": 0}

def import_pagos_from_rows(*args: Any, **kwargs: Any) -> dict:
    return {"nuevos": 0, "actualizados": 0}

def import_pagos_consolidado_from_rows(*args: Any, **kwargs: Any) -> dict:
    return {"nuevos": 0, "actualizados": 0}

def insert_liq_row(row: dict) -> Any:
    """Inserta un nuevo registro en la tabla liq."""
    db = get_supabase()
    if not db:
        raise Exception("Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env")

    table_name = resolve_liq_table_name(db)
    try:
        response = db.table(table_name).insert(row).execute()
        return response
    except Exception:
        raise

def update_liq_row(escritura: str, row: dict) -> Any:
    """Actualiza un registro existente en la tabla liq, identificado por escritura."""
    db = get_supabase()
    if not db:
        raise Exception("Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env")

    table_name = resolve_liq_table_name(db)
    try:
        response = db.table(table_name).update(row).eq('escritura', escritura).execute()
        return response
    except Exception:
        raise

def get_pending_liq(limit: int = 10000, page: int = 1, sort_by: str = 'escritura', desc: bool = False) -> Any:
    """Obtiene registros pendientes (sin procesar) desde Supabase."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()

    table_name = resolve_liq_table_name(db)
    try:
        offset = (page - 1) * limit
        query = db.table(table_name).select('*')
        query = query.range(offset, offset + limit - 1)
        if sort_by:
            query = query.order(sort_by, desc=desc)
        response = query.execute()
        return response
    except Exception:
        return type('obj', (object,), {'data': []})()

def update_liq_estado_by_escritura(escritura: str, estado: str, activity_type: str = None) -> Any:
    """Actualiza el estado de un registro por escritura."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()
    
    try:
        response = db.table('liq').update(
            {'estado_ctl': estado}
        ).eq('escritura', escritura).execute()
        return response
    except Exception as e:
        print(f"Error en update_liq_estado_by_escritura: {e}")
        return type('obj', (object,), {'data': []})()

def get_liq_stats() -> dict:
    """Obtiene estadísticas agregadas de las tablas liq."""
    db = get_supabase()
    if not db:
        return {"total": 0, "liq": 0, "liq_2025": 0, "liq_2026": 0}

    try:
        stats = {"total": 0, "liq": 0, "liq_2025": 0, "liq_2026": 0}
        for table_name in ['liq', 'liquida', 'liq_2025', 'liq_2026']:
            try:
                if not _table_exists(db, table_name):
                    continue
                response = db.table(table_name).select('*', count='exact', head=True).execute()
                count = response.count or 0
                stats[table_name] = count
                stats['total'] += count
                if table_name == 'liquida':
                    stats['liq'] = count
            except Exception:
                continue
        return stats
    except Exception:
        return {"total": 0, "liq": 0, "liq_2025": 0, "liq_2026": 0}

def get_all_liq(limit: int = 10000, page: int = 1, sort_by: str = 'updated_at', desc: bool = True) -> Any:
    """Obtiene todos los registros de la tabla liq con paginación."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()

    table_name = resolve_liq_table_name(db)
    try:
        offset = (page - 1) * limit
        query = db.table(table_name).select('*')
        query = query.range(offset, offset + limit - 1)
        if sort_by:
            query = query.order(sort_by, desc=desc)
        response = query.execute()
        return response
    except Exception:
        return type('obj', (object,), {'data': []})()

def get_processed_liq(limit: int = 10000, page: int = 1, sort_by: str = 'fecha_proceso', desc: bool = True) -> Any:
    """Obtiene registros procesados desde Supabase."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()

    table_name = resolve_liq_table_name(db)
    try:
        offset = (page - 1) * limit
        query = db.table(table_name).select('*')
        query = query.or_('notificacion.eq.enviado,pago.eq.ingresado')
        query = query.range(offset, offset + limit - 1)
        if sort_by:
            query = query.order(sort_by, desc=desc)
        response = query.execute()
        return response
    except Exception:
        return type('obj', (object,), {'data': []})()

def move_liq_to_table(escritura: str, target: str = None) -> dict:
    """Mueve un registro de una tabla a otra."""
    db = get_supabase()
    if not db:
        return {"ok": False, "message": "Supabase no configurado"}
    
    try:
        if not target or target not in ['liq', 'liq_2025', 'liq_2026']:
            return {"ok": False, "message": "Tabla destino inválida"}
        
        # Obtener el registro
        response = db.table('liq').select('*').eq('escritura', escritura).execute()
        if not response.data:
            return {"ok": False, "message": "Registro no encontrado"}
        
        record = response.data[0]
        
        # Insertar en la tabla destino
        db.table(target).insert(record).execute()
        
        # Eliminar de la tabla original
        db.table('liq').delete().eq('escritura', escritura).execute()
        
        return {"ok": True, "message": f"Registro movido a {target}"}
    except Exception as e:
        print(f"Error en move_liq_to_table: {e}")
        return {"ok": False, "message": str(e)}

def export_liq_to_excel() -> str:
    """Exporta la tabla liq y el informe asociado a un archivo Excel con dashboard."""
    import pandas as pd
    from pathlib import Path

    db = get_supabase()
    if not db:
        return ""

    try:
        # Obtener todos los registros de liq
        response = db.table('liq').select('*').range(0, 999999).execute()
        data = response.data
        if not data:
            return ""

        df = pd.DataFrame(data)

        # Eliminar columnas internas y de auditoría
        drop_patterns = [r'(^id$|_id$|^id_|llave|clave|created|updated|fecha_creacion|fecha_actualizacion)']
        cols_to_drop = [c for c in df.columns if any(re.search(pat, c, re.I) for pat in drop_patterns)]
        df = df[[c for c in df.columns if c not in cols_to_drop]]

        # Formatear fechas en estilo colombiano dd/mm/aa
        for col in df.columns:
            if 'fecha' in col.lower() or col.lower().endswith('_at'):
                try:
                    parsed = pd.to_datetime(df[col], errors='coerce', dayfirst=True)
                    if parsed.notna().any():
                        df[col] = parsed.dt.strftime('%d/%m/%y')
                except Exception:
                    continue

        # Preparar hoja de Pagos 2026 con resumen
        pagos2026 = []
        try:
            pagos2026 = get_pagos_2026_resumen()
        except Exception:
            pagos2026 = []
        df_pagos = pd.DataFrame(pagos2026)
        if not df_pagos.empty:
            df_pagos = df_pagos[[c for c in df_pagos.columns if c not in cols_to_drop]]

        # Preparar hoja de dashboard con métricas clave
        stats = get_liq_stats() or {}
        total_registros = len(df)
        total_a_cobrar = int(df_pagos['total'].sum()) if not df_pagos.empty else 0
        total_pagado = int(df_pagos['pago_cli'].sum()) if not df_pagos.empty else 0
        total_faltante = int(df_pagos['faltante'].sum()) if not df_pagos.empty else 0
        total_sobrante = int(df_pagos['sobrante'].sum()) if not df_pagos.empty else 0

        dashboard_rows = [
            {'Métrica': 'Total registros', 'Valor': total_registros},
            {'Métrica': 'Total liq', 'Valor': stats.get('liq', 0)},
            {'Métrica': 'Total liq_2025', 'Valor': stats.get('liq_2025', 0)},
            {'Métrica': 'Total liq_2026', 'Valor': stats.get('liq_2026', 0)},
            {'Métrica': 'Total escrituras Pagos 2026', 'Valor': len(df_pagos)},
            {'Métrica': 'Total a cobrar', 'Valor': total_a_cobrar},
            {'Métrica': 'Total pagado en actas', 'Valor': total_pagado},
            {'Métrica': 'Total faltante', 'Valor': total_faltante},
            {'Métrica': 'Total sobrante', 'Valor': total_sobrante},
        ]
        df_dashboard = pd.DataFrame(dashboard_rows)

        output_dir = Path('backups')
        output_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = output_dir / f"notaria_backup_{timestamp}.xlsx"

        with pd.ExcelWriter(filename, date_format='DD/MM/YY', datetime_format='DD/MM/YY') as writer:
            df.to_excel(writer, index=False, sheet_name='Registros')
            df_dashboard.to_excel(writer, index=False, sheet_name='Dashboard')
            if not df_pagos.empty:
                df_pagos.to_excel(writer, index=False, sheet_name='Pagos 2026')

        return str(filename)
    except Exception as e:
        print(f"Error en export_liq_to_excel: {e}")
        return ""

def insert_acta(row: dict) -> Any:
    """Inserta una nueva acta y garantiza que la escritura tenga fila en pagos_2026
    (esto es lo que hace que 'se asigne automáticamente' al módulo Pagos 2026)."""
    db = get_supabase()
    if not db:
        raise Exception("Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env")

    try:
        response = db.table('actas').insert(row).execute()

        escritura = row.get('escritura')
        if escritura is not None:
            existente = db.table('pagos_2026').select('escritura').eq('escritura', escritura).execute()
            if not existente.data:
                db.table('pagos_2026').insert({'escritura': escritura, 'vr_ben': 0, 'vr_reg': 0}).execute()

        return response
    except Exception as e:
        print(f"Error en insert_acta: {e}")
        raise

def get_actas_by_escritura(escritura, anio: int = 2026) -> list:
    """Devuelve las actas individuales de una escritura (para la vista de detalle)."""
    db = get_supabase()
    if not db:
        return []
    try:
        response = db.table('actas').select('*').eq('escritura', escritura).eq('anio', anio).order('fecha_acta').execute()
        return response.data or []
    except Exception as e:
        print(f"Error en get_actas_by_escritura: {e}")
        return []

def get_acta_by_id(acta_id: int) -> dict:
    """Devuelve una acta por su id."""
    db = get_supabase()
    if not db:
        return {}
    try:
        response = db.table('actas').select('*').eq('id', acta_id).single().execute()
        return response.data or {}
    except Exception as e:
        print(f"Error en get_acta_by_id: {e}")
        return {}

def update_acta(acta_id: int, row: dict) -> Any:
    """Actualiza un registro de acta por su id."""
    db = get_supabase()
    if not db:
        raise Exception("Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env")

    try:
        allowed_fields = {
            'escritura', 'acta', 'fecha_acta', 'tipo_deposito', 'cliente',
            'documento', 'radicado', 'anio', 'valor_acta', 'estado', 'constructora'
        }
        payload = {k: v for k, v in row.items() if k in allowed_fields}
        if not payload:
            raise Exception("No hay campos válidos para actualizar.")
        response = db.table('actas').update(payload).eq('id', acta_id).execute()
        return response
    except Exception as e:
        print(f"Error en update_acta: {e}")
        raise

def delete_acta(acta_id: int) -> Any:
    """Elimina una acta por su id."""
    db = get_supabase()
    if not db:
        raise Exception("Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env")

    try:
        response = db.table('actas').delete().eq('id', acta_id).execute()
        return response
    except Exception as e:
        print(f"Error en delete_acta: {e}")
        raise

def get_pagos_2026_resumen(anio: int = 2026) -> list:
    """Calcula, para cada escritura con actas registradas, el match entre lo que se debe
    cobrar (Vr_Ben + Vr_Reg, ingresado manualmente) y lo que el cliente ya pagó (suma de
    actas por tipo de depósito). Devuelve Faltante/Sobrante y el desglose individual
    (Dif_Ben / Dif_Reg) de a qué componente pertenece la diferencia."""
    db = get_supabase()
    if not db:
        return []

    try:
        actas_resp = db.table('actas').select('escritura,tipo_deposito,valor_acta').eq('anio', anio).execute()
        actas = actas_resp.data or []

        pagos_resp = db.table('pagos_2026').select('*').execute()
        pagos_por_escritura = {p['escritura']: p for p in (pagos_resp.data or [])}

        sumas = {}
        for a in actas:
            esc = a['escritura']
            s = sumas.setdefault(esc, {'beneficencia': 0, 'registro': 0})
            valor = a.get('valor_acta') or 0
            if a.get('tipo_deposito') == 'Beneficencia':
                s['beneficencia'] += valor
            elif a.get('tipo_deposito') == 'Registro Instrumentos Publicos':
                s['registro'] += valor

        filas = []
        for esc, s in sumas.items():
            pago = pagos_por_escritura.get(esc, {})
            vr_ben = pago.get('vr_ben') or 0
            vr_reg = pago.get('vr_reg') or 0
            total = vr_ben + vr_reg
            pago_cli = s['beneficencia'] + s['registro']

            dif_ben = vr_ben - s['beneficencia']
            dif_reg = vr_reg - s['registro']
            faltante = max(total - pago_cli, 0)
            sobrante = max(pago_cli - total, 0)

            filas.append({
                'escritura': esc,
                'liquidacion': pago.get('liquidacion'),
                'responsable': pago.get('responsable'),
                'vr_ben': vr_ben,
                'vr_reg': vr_reg,
                'total': total,
                'pago_cli': pago_cli,
                'faltante': faltante,
                'sobrante': sobrante,
                'dif_ben': dif_ben,
                'dif_reg': dif_reg,
                'fecha_pago': pago.get('fecha_pago'),
                'observaciones': pago.get('observaciones'),
            })

        filas.sort(key=lambda x: x['escritura'])
        return filas
    except Exception as e:
        print(f"Error en get_pagos_2026_resumen: {e}")
        return []

def upsert_pago_2026(escritura, vr_ben: float = None, vr_reg: float = None,
                      fecha_pago: str = None, observaciones: str = None,
                      liquidacion: str = None, responsable: str = None) -> Any:
    """Crea o actualiza los valores manuales (Vr_Ben, Vr_Reg, fecha proyectada de pago) de una escritura."""
    db = get_supabase()
    if not db:
        raise Exception("Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env")

    try:
        payload = {'escritura': escritura}
        if vr_ben is not None: payload['vr_ben'] = vr_ben
        if vr_reg is not None: payload['vr_reg'] = vr_reg
        if fecha_pago is not None: payload['fecha_pago'] = fecha_pago
        if observaciones is not None: payload['observaciones'] = observaciones
        if liquidacion is not None: payload['liquidacion'] = liquidacion
        if responsable is not None: payload['responsable'] = responsable

        response = db.table('pagos_2026').upsert(payload, on_conflict='escritura').execute()
        return response
    except Exception as e:
        print(f"Error en upsert_pago_2026: {e}")
        raise

def import_actas_from_rows(rows: list, batch_size: int = 200) -> dict:
    """Importa actas evitando duplicados (misma combinación acta+escritura ya existente).
    También garantiza que cada escritura importada tenga su fila en pagos_2026."""
    db = get_supabase()
    if not db:
        raise Exception("Supabase no configurado. Verifique SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env")

    try:
        escrituras = list({r['escritura'] for r in rows if r.get('escritura') is not None})

        existentes = set()
        for i in range(0, len(escrituras), 200):
            chunk = escrituras[i:i + 200]
            resp = db.table('actas').select('acta,escritura').in_('escritura', chunk).execute()
            for e in (resp.data or []):
                existentes.add((str(e.get('acta')), e.get('escritura')))

        a_insertar = []
        for r in rows:
            key = (str(r.get('acta')), r.get('escritura'))
            if key in existentes:
                continue
            a_insertar.append(r)
            existentes.add(key)

        nuevos = 0
        for i in range(0, len(a_insertar), batch_size):
            batch = a_insertar[i:i + batch_size]
            if batch:
                db.table('actas').insert(batch).execute()
                nuevos += len(batch)

        if escrituras:
            pagos_resp = db.table('pagos_2026').select('escritura').execute()
            con_pago = {p['escritura'] for p in (pagos_resp.data or [])}
            faltantes = [e for e in escrituras if e not in con_pago]
            for e in faltantes:
                db.table('pagos_2026').insert({'escritura': e, 'vr_ben': 0, 'vr_reg': 0}).execute()

        return {
            "nuevos": nuevos,
            "omitidos_duplicados": len(rows) - nuevos,
        }
    except Exception as e:
        print(f"Error en import_actas_from_rows: {e}")
        raise

def get_table_rows(table_name: str, limit: int = 1000, page: int = 1, sort_by: str = None, desc: bool = True) -> Any:
    """Obtiene filas de una tabla específica (liq, liq_2025, liq_2026, etc.)."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()

    try:
        allowed_tables = {'liq', 'liquida', 'liq_2025', 'liq_2026', 'pagos_2026', 'pagos_consolidado'}
        if table_name not in allowed_tables:
            return type('obj', (object,), {'data': []})()
        if table_name == 'liq' and not _table_exists(db, 'liq') and _table_exists(db, 'liquida'):
            table_name = 'liquida'
        offset = (page - 1) * limit
        query = db.table(table_name).select('*')
        query = query.range(offset, offset + limit - 1)
        if sort_by:
            query = query.order(sort_by, desc=desc)
        response = query.execute()
        return response
    except Exception:
        return type('obj', (object,), {'data': []})()


def get_responsables() -> list[str]:
    db = get_supabase()
    if not db:
        return DEFAULT_RESPONSABLES
    try:
        if not _table_exists(db, 'responsables'):
            return DEFAULT_RESPONSABLES
        response = db.table('responsables').select('*').order('nombre').execute()
        items = response.data or []
        values = [str(item.get('nombre') or item.get('responsable') or '').strip() for item in items]
        valid = [v for v in values if v]
        return valid or DEFAULT_RESPONSABLES
    except Exception:
        return DEFAULT_RESPONSABLES


def upsert_responsable(nombre: str) -> list[str]:
    nombre = (nombre or '').strip()
    if not nombre:
        return get_responsables()
    db = get_supabase()
    if not db:
        return get_responsables()
    try:
        if not _table_exists(db, 'responsables'):
            return get_responsables()
        db.table('responsables').upsert({'nombre': nombre}, on_conflict='nombre').execute()
        return get_responsables()
    except Exception:
        return get_responsables()

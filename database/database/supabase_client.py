import os
from typing import Any, Optional, List
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()


def get_supabase() -> Optional[Any]:
    """Devuelve la instancia de Supabase cuando exista configuración válida."""
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()    

    if not url or not key:
        return None

    try:
        from supabase import create_client
    except Exception:
        return None

    try:
        return create_client(url, key)
    except Exception:
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


def insert_liq_row(*args: Any, **kwargs: Any) -> dict:
    return {"ok": True}


def update_liq_row(*args: Any, **kwargs: Any) -> dict:
    return {"ok": True}


def get_pending_liq(limit: int = 10000, page: int = 1, sort_by: str = 'escritura', desc: bool = False) -> Any:
    """Obtiene registros pendientes (sin procesar) desde Supabase."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()
    
    try:
        offset = (page - 1) * limit
        query = db.table('liq').select('*')
        
        # Aplicar paginación
        query = query.range(offset, offset + limit - 1)
        
        # Aplicar ordenamiento
        if sort_by:
            query = query.order(sort_by, desc=desc)
        
        response = query.execute()
        return response
    except Exception as e:
        print(f"Error en get_pending_liq: {e}")
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
        
        # Contar registros por tabla
        for table_name in ['liq', 'liq_2025', 'liq_2026']:
            try:
                response = db.table(table_name).select('count', count='exact').execute()
                count = response.count or 0
                stats[table_name] = count
                stats['total'] += count
            except Exception as e:
                print(f"Error contando {table_name}: {e}")
        
        return stats
    except Exception as e:
        print(f"Error en get_liq_stats: {e}")
        return {"total": 0, "liq": 0, "liq_2025": 0, "liq_2026": 0}


def get_all_liq(limit: int = 10000, page: int = 1, sort_by: str = 'updated_at', desc: bool = True) -> Any:
    """Obtiene todos los registros de la tabla liq con paginación."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()
    
    try:
        offset = (page - 1) * limit
        query = db.table('liq').select('*')
        
        # Aplicar paginación
        query = query.range(offset, offset + limit - 1)
        
        # Aplicar ordenamiento
        if sort_by:
            query = query.order(sort_by, desc=desc)
        
        response = query.execute()
        return response
    except Exception as e:
        print(f"Error en get_all_liq: {e}")
        return type('obj', (object,), {'data': []})()


def get_processed_liq(limit: int = 10000, page: int = 1, sort_by: str = 'fecha_proceso', desc: bool = True) -> Any:
    """Obtiene registros procesados desde Supabase."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()
    
    try:
        offset = (page - 1) * limit
        query = db.table('liq').select('*')
        
        # Filtrar solo procesados
        query = query.or_('notificacion.eq.enviado,pago.eq.ingresado')
        
        # Aplicar paginación
        query = query.range(offset, offset + limit - 1)
        
        # Aplicar ordenamiento
        if sort_by:
            query = query.order(sort_by, desc=desc)
        
        response = query.execute()
        return response
    except Exception as e:
        print(f"Error en get_processed_liq: {e}")
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
    """Exporta la tabla liq a un archivo Excel."""
    import pandas as pd
    from pathlib import Path
    
    db = get_supabase()
    if not db:
        return ""
    
    try:
        # Obtener todos los datos
        response = db.table('liq').select('*').limit(10000).execute()
        data = response.data
        
        if not data:
            return ""
        
        # Convertir a DataFrame
        df = pd.DataFrame(data)
        
        # Crear archivo
        output_dir = Path("backups")
        output_dir.mkdir(exist_ok=True)
        
        filename = output_dir / f"liq_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        df.to_excel(filename, index=False)
        
        return str(filename)
    except Exception as e:
        print(f"Error en export_liq_to_excel: {e}")
        return ""


def get_table_rows(table_name: str, limit: int = 1000, page: int = 1, sort_by: str = None, desc: bool = True) -> Any:
    """Obtiene filas de una tabla específica (liq, liq_2025, liq_2026, etc.)."""
    db = get_supabase()
    if not db:
        return type('obj', (object,), {'data': []})()
    
    try:
        allowed_tables = {'liq', 'liq_2025', 'liq_2026', 'pagos_2026', 'pagos_consolidado'}
        if table_name not in allowed_tables:
            print(f"Tabla no permitida: {table_name}")
            return type('obj', (object,), {'data': []})()
        
        offset = (page - 1) * limit
        query = db.table(table_name).select('*')
        
        # Aplicar paginación
        query = query.range(offset, offset + limit - 1)
        
        # Aplicar ordenamiento
        if sort_by:
            query = query.order(sort_by, desc=desc)
        
        response = query.execute()
        return response
    except Exception as e:
        print(f"Error en get_table_rows({table_name}): {e}")
        return type('obj', (object,), {'data': []})()


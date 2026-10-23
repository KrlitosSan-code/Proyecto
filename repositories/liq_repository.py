from database.supabase_client import get_supabase

class LiqRepository:
    
    def __init__(self):
        self.supabase = get_supabase()
        
    def recibos_pendientes(self):
        return (
            self.supabase.table("liq")
            .select("*")
            .eq("notificacion", "pendiente")
            .execute().data
        )
        
    def certificados_pendientes(self):
        pendientes = []
        for table_name in ("liq", "liq_2025"):
            result = (
                self.supabase
                .table(table_name)
                .select("*")
                .eq("pago", "ingresado")
                .eq("notificacion", "enviado")
                .eq("estado_ctl", "pendiente")
                .execute()
            )
            for row in result.data or []:
                pendientes.append({**row, "tabla_origen": table_name})
        return pendientes

    def actualizar_estado_ctl(self, escritura, estado, tabla_origen=None):
        tablas = [tabla_origen] if tabla_origen in {"liq", "liq_2025"} else ["liq", "liq_2025"]
        for table_name in tablas:
            result = (
                self.supabase.table(table_name)
                .update({"estado_ctl": estado})
                .eq("escritura", escritura)
                .execute()
            )
            if result.data:
                return result
        return type("Response", (), {"data": []})()
    
    def certificados_procesando(self):
        rows = []
        for table_name in ("liq", "liq_2025"):
            result = (
                self.supabase.table(table_name)
                .select("*")
                .eq("estado_ctl", "procesando")
                .execute()
            )
            rows.extend({**row, "tabla_origen": table_name} for row in (result.data or []))
        return rows
    
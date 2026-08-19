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
        result = (
            self.supabase
            .table("liq")
            .select("*")
            .eq("pago", "ingresado")
            .eq("notificacion", "enviado")
            .neq("estado_ctl", "procesando")
            .execute()
        )
        return result.data or []
    
    def certificados_procesando(self):
        result = (
            self.supabase
            .table("liq")
            .select("*")
            .eq("estado_ctl", "procesando")
            .execute()
        )
        return result.data or []
    
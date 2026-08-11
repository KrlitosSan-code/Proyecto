from database.supabase_client import get_supabase


class DescargasRepository:

    def __init__(self):

        self.supabase = get_supabase()

    def guardar_descarga(
        self,
        escritura,
        tipo,
        cantidad_archivos,
        correo=None
    ):

        payload = {
            "escritura": escritura,
            "tipo": tipo,
            "cantidad_archivos": cantidad_archivos,
            "correo": correo,
        }

        return (
            self.supabase
            .table("descargas")
            .insert(payload)
            .execute()
        )

    def pendientes_envio(
        self,
        tipo="certificado"
    ):

        result = (
            self.supabase
            .table("descargas")
            .select("*")
            .eq("tipo", tipo)
            .eq("enviado", False)
            .execute()
        )
        return result.data or []

    def marcar_enviado(
        self,
        escritura
    ):

        return (
            self.supabase
            .table("descargas")
            .update({
                "enviado": True
            })
            .eq("escritura", escritura)
            .execute()
        )
        
    def pendientes_envio_certificados(self):
        
        result = (
            self.supabase
            .table("descargas")
            .select("*")
            .eq("tipo", "certificado")
            .eq("enviado", False)
            .execute()
        )
        return result.data or []
from database.supabase_client import get_supabase


class LogRepository:

    def __init__(self):

        self.supabase = get_supabase()

    def registrar(
        self,
        escritura,
        proceso,
        estado,
        mensaje
    ):
        payload = {
            "escritura": escritura,
            "proceso": proceso,
            "estado": estado,
            "mensaje": mensaje
        }
        return (
            self.supabase
            .table("logss")
            .insert(payload)
            .execute()
        )
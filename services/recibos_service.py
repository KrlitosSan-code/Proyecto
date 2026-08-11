from pathlib import Path
import re

from repositories.liq_repository import LiqRepository

from templates.estados import Notificacion
from database.settings import DOWNLOADS_RECIBOS

class RecibosService:

    def __init__(self):

        self.repo = LiqRepository()

    def pendientes(self):

        return self.repo.recibos_pendientes()

    def marcar_enviado(
        self,
        escritura
    ):

        self.repo.supabase.table("liq").update({

            "notificacion": Notificacion.ENVIADO

        }).eq(

            "escritura",
            escritura

        ).execute()
    
    def buscar_archivos_recibo(
            self,
            escritura
    ):
        carpeta = Path("C:\\descargas\\recibos")
        escritura_str = str(escritura)
        # Busca el número exacto no pegado a otros dígitos
        patron = re.compile(rf'(?<!\d){re.escape(escritura_str)}(?!\d)')
        
        archivos = []
        # Buscar en la raíz
        for p in carpeta.iterdir():
            if p.is_file() and patron.search(p.name):
                archivos.append(p)
        
        # Si no encuentra en la raíz, buscar en subcarpetas
        if not archivos:
            for sub in carpeta.iterdir():
                if sub.is_dir():
                    for p in sub.iterdir():
                        if p.is_file() and patron.search(p.name):
                            archivos.append(p)             
        return archivos
        
    def marcar_error(
        self,
        escritura
    ):
        self.repo.supabase.table(
            "logss"
        ).update({
            "estado": "Error"
        }).eq(
            "escritura",
            escritura
        ).execute()
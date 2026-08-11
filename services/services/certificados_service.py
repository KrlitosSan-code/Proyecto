from concurrent.futures import wait
import hashlib
from logging import log
import time
import os
from typing import List, Optional
from repositories.descargas_repository import DescargasRepository
from repositories.liq_repository import LiqRepository
from templates.estados import EstadoCTL
from pathlib import Path
from playwright.sync_api import sync_playwright
import re

class CertificadosService:

    def __init__(self):
        self.repo = LiqRepository()
        self.repo_descargas = DescargasRepository()
        
    def obtener_pendientes(self):
        return self.repo.certificados_pendientes()

    def obtener_procesando(self):
        return self.repo.certificados_procesando()   
    
    def marcar_procesando(
        self,
        escritura
    ):
        self.repo.supabase.table("liq").update({
            "estado_ctl": EstadoCTL.PROCESANDO
        }).eq(
            "escritura",
            escritura
        ).execute()

    def marcar_enviado(
        self,
        escritura
    ):
        self.repo.supabase.table("liq").update({
            "estado_ctl": EstadoCTL.ENVIADO
        }).eq(
            "escritura",
            escritura
        ).execute()
    
    def marcar_error(
        self,
        escritura
    ):
        self.repo.supabase.table("logss").update({
            "estado": EstadoCTL.ERROR
        }).eq(
            "escritura",
            escritura
        ).execute()    
    
    def registrar_descarga(
        self,
        row,
        cantidad_archivos,
    ):
        self.repo_descargas.guardar_descarga(
            escritura = row["escritura"],
            tipo = "certificado",
            cantidad_archivos = cantidad_archivos,
            correo = row["correo"]
        )
              
    def esperar_descarga_pdf(carpeta: str, started_at: float, timeout: int = 120) -> Optional[str]:
        """Espera un PDF nuevo/actualizado después de started_at."""
        fin = time.time() + timeout
        while time.time() < fin:
            candidatos = []
            for f in os.listdir(carpeta):
                path = os.path.join(carpeta, f)
                if not os.path.isfile(path):
                    continue
                if not f.lower().endswith(".pdf"):
                    continue
                if os.path.getmtime(path) >= started_at:
                    candidatos.append(path)
            if candidatos:
                candidatos.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                return candidatos[0]
            time.sleep(1)
        return None
        
    def buscar_archivos_certificado(self, escritura):
        carpeta = Path("C:\\descargas\\certificados")
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
        
        # Log opcional
        print(f"Archivos encontrados para {escritura}: {[p.name for p in archivos]}")
        return archivos
        
import asyncio
import re
import threading
from playwright.sync_api import sync_playwright
from database.settings import DOWNLOADS_DIR

from services.playwright_manager import (
    PlaywrightManager
)
from services.certificados_service import (
    CertificadosService
)
from services.gmail_playwright_service import (
    GmailPlaywrightService
)

from repositories.log_repository import (
    LogRepository
)
from services.recibos_service import (
    RecibosService
)

from repositories.descargas_repository import (
    DescargasRepository
)

from services.supernotariado_service import SupernotariadoService
from templates.email_templates import (
    asunto_certificado,
    cargar_cuerpo_certificado,
    asunto_recibo,
    cargar_cuerpo_recibo
)


class WorkflowService:
   
    def __init__(
        self,
        gmail_service=None,
        certificados_service=None,
        logs_repository=None,
        recibos_service=None
    ):
        self.browser = PlaywrightManager()
        self.supernotariado = (
            SupernotariadoService(
                context=self.browser.context
            )
        )
        self.descargas = DescargasRepository()
        self.gmail = (
            gmail_service
            if gmail_service is not None
            else GmailPlaywrightService(
                context=self.browser.context
            )
        ) 
        self.certificados = certificados_service if certificados_service is not None else CertificadosService()
        self.logs = logs_repository if logs_repository is not None else LogRepository()
        self.recibos = recibos_service if recibos_service is not None else RecibosService()
    
    def ejecutar_certificados(self):        

        pendientes = self.certificados.obtener_pendientes()
        page = self.supernotariado.abrir_certificados()   

        print("PAGINAS:", len(self.browser.context.pages))
        try:
            for row in pendientes:
                escritura = row["escritura"]
                try:
                    
                    self.logs.registrar(
                        escritura=escritura,
                        proceso="certificado",
                        estado="inicio",
                        mensaje="Inicia proceso"
                    )
                        
                    archivos = self.supernotariado.descargar_certificados(
                        page=page,
                        escritura=escritura,
                        nir=str(row["nir"]),
                        )
                        
                    if archivos:
                        self.certificados.marcar_procesando(escritura)

                        self.logs.registrar(
                            escritura=escritura,
                            proceso="certificado",
                            estado="procesando",
                            mensaje="Procesando certificado"
                        )                    

                        self.logs.registrar(
                            escritura=escritura,
                            proceso="certificado",
                            estado="finzalizado",
                            mensaje=f"{len(archivos)} archivo(s) procesado(s)"
                        )
                            
                    else:
                            self.logs.registrar(
                                escritura=escritura,
                                proceso="certificado",
                                estado="sin_archivos",
                                mensaje="No se generaron archivos para este certificado"
                            )                      

                except Exception as e:

                    self.certificados.marcar_error(
                        escritura
                    )
                    try:
                        page.keyboard.press("Escape")
                    except:
                        pass

                    self.logs.registrar(
                        escritura=escritura,
                        proceso="certificado",
                        estado="error",
                        mensaje=str(e)
                    )
                    print(f"ERROR {escritura} -> {e}")             
                    print("PAGINAS:", len(self.browser.context.pages))

        finally:
            self.browser.close()   
        
    def ejecutar_recibos(self):
        pendientes = self.recibos.pendientes()

        print(f"recibos pendientes: {len(pendientes)}")
        try:
            for row in pendientes:
                escritura = row['escritura']
                nir = row['nir']
                try:
                    archivos = self.recibos.buscar_archivos_recibo(escritura)
                    if not archivos:
                        self.logs.registrar(
                            escritura=escritura,
                            proceso="recibo",
                            estado="error",
                            mensaje="No se encontraron recibos"
                        )
                        continue                
                                

                    self.gmail.crear_borrador(
                        correo=row['correo'],
                        asunto=asunto_recibo(escritura, nir),
                        cuerpo=cargar_cuerpo_recibo(escritura),
                        adjuntos=archivos
                    )
                    print(f"Correo enviado: {escritura}")
                    self.recibos.marcar_enviado(escritura)
                    
                except Exception as e:
                    self.logs.registrar(
                        escritura=escritura,
                        proceso="recibo",
                        estado="error",
                        mensaje=str(e)
                    )
                    print(f"ERROR {row['escritura']} -> {e}")
                print('Envio de recibos finalizado')
        finally:
            self.browser.close()

    def ejecutar_envio_certificados(self):
        pendientes = self.certificados.obtener_procesando()
        
        print(f"Pendientes de envio: {len(pendientes)}")     
        try:
            for row in pendientes:
                escritura = row["escritura"]
                
                try:               
                    archivos = self.certificados.buscar_archivos_certificado(escritura)
                    if not archivos:
                        self.logs.registrar(
                            escritura=escritura,
                            proceso="envio_certificado",
                            estado="error",
                            mensaje="No hay archivos"
                        )
                        continue                
                    print(f"Intentando crear borrador para {escritura}")
                    print(f"Correo: {row['correo']}")
                    print(f"Asunto: {asunto_certificado(escritura)}")
                    print(f"Adjuntos: {len(archivos)}")
                    self.gmail.crear_borrador(
                        correo=row["correo"],
                        asunto=asunto_certificado(escritura),
                        cuerpo=cargar_cuerpo_certificado(escritura),
                        adjuntos=archivos
                    )                
                    print("Borrador Creado")     
                    
                    self.certificados.marcar_enviado(escritura)
                    
                except Exception as e:
                    self.logs.registrar(
                        escritura=escritura,
                        proceso="envio_certificado",
                        estado="error",
                        mensaje=str(e)
                    )
                    print(f"Error envio {escritura} ->{e}")
            print("ENVIO CERTIFICADOS EJECUTADO")

        finally:
            self.browser.close()
            
    def run_workflow(mode: str = "certificados"):

        print(f"MODE: {mode}")
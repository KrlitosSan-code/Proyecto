import re

from database.settings import DOWNLOADS_DIR


class SupernotariadoService:

    URL =("https://radicacion.supernotariado.gov.co/app/inicio.dma")

    def __init__(self, context):
        self.context = context

    def abrir_certificados(self):
        page = self.context.new_page()
        page.goto(self.URL)
        page.wait_for_timeout(3000)
        
        page.get_by_role(
            "link",
            name="Certificados"
        ).click()

        page.wait_for_timeout(2000)
        print("ABRIENDO PAGINA SUPERNOTARIADO")
        return page

    def descargar_certificados(
            self,
            page,
            escritura,
            nir
        ):               
        
        self.buscar_nir(page,nir)

        self.validar_resultado(
            page,
            escritura,
            nir
        )
        matriculas = self.obtener_matriculas(
            page
        )
        return self.descargar_archivos(
            page,
            escritura,
            matriculas
        )

    def buscar_nir (
            self,
            page,
            nir
    ):
        campo_nir = page.locator(
            "#formSearch\\:j_idt44"
        )
        
        campo_nir.fill("")
        campo_nir.fill(nir)
        
        page.locator(
            "#formSearch\\:j_idt45"
        ).click()
        
        page.wait_for_timeout(5000)

    def validar_resultado(
            self,
            page,
            escritura,
            nir
    ):
        modal = page.locator(
            "#modalDialog"
        )
        
        if modal.is_visible():
                texto = modal.inner_text()
                if "No existen certificados" in texto:
                    try:
                        page.keyboard.press("Escape")
                    except:
                        pass
        
                    raise Exception(
                        f"No existen certificados para {escritura} - NIR: {nir}"
                    )

    def obtener_matriculas(
            self,
            page
    ):
        contenido = page.locator(
            "body"
        ).inner_text()
        
        matriculas = re.findall(
            r"Matricula:\s*(\d+)",
            contenido
        )
        
        print(
            f"Matriculas:{len(matriculas)}"
        )
        return matriculas

    def descargar_archivos(
            self,
            page,
            escritura,
            matriculas
    ):
        botones = page.get_by_text(
            "Descargar",
            exact=True
        )
        
        total = botones.count()
        
        if total == 0:
            print(f"No ha sido calificada la escritura: {escritura}")  
            return[]      
        
        rutas_guardadas = []
        
        for i in range(total):
        
            print(
                f"Descargando {i+1}/{total}"
            )
        
            if i >= len(matriculas):
                print(f'Matriculas pendientes de calificación para: {escritura}')
                return[]
        
            matricula = matriculas[i]
        
            with page.expect_download() as descarga:
        
                botones.nth(i).click()
        
            archivo = descarga.value
        
            destino = (
                DOWNLOADS_DIR
                / f"{escritura} Matricula {matricula}.pdf"
            )
        
            archivo.save_as(
                str(destino)
            )
        
            rutas_guardadas.append(
                str(destino)
            )        
        
        print(
            f"Guardado: {destino}"
        )
        
        return rutas_guardadas
from playwright.sync_api import sync_playwright
from database import settings


PROFILE = settings.EDGE_PROFILE


class GmailPlaywrightService:

    def __init__(self, context):

        self.context = context
        self.page = None   

    def crear_borrador(
        self,
        correo,
        asunto,
        cuerpo,
        adjuntos=None
    ):        
        if self.page is None:
            self.page = self.context.new_page()

        self.page.goto("https://mail.google.com/mail/u/0/#inbox?compose=new")
        self.page.wait_for_timeout(3000)
        self.page.wait_for_selector("input[aria-label='Destinatarios']").fill(correo)
        self.page.locator("input[aria-label='Asunto']").fill(asunto)
        self.page.locator("div[aria-label='Cuerpo del mensaje']").fill(cuerpo)
        self.page.wait_for_timeout(2000)
        if adjuntos:
            input_file = self.page.locator("input[type='file']")
            input_file.set_input_files(adjuntos)
            self.page.wait_for_timeout(3000)
            #self.page.keyboard.press("Control+Enter")
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(3000)
        print(len(self.context.pages))
        
from playwright.sync_api import sync_playwright

class BrowserService:
    def iniciar(self):
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            channel="msedge",
            headless=False
        )
        
        page = browser.new_page()
        return playwright, browser, page
    
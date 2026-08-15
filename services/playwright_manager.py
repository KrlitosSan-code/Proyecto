from playwright.sync_api import sync_playwright
from database import settings
class PlaywrightManager:

    def __init__(self):
        self.playwright = sync_playwright().start()
        print("CONTEXTO CREADO")

        args = []

        if settings.EDGE_PROFILE:
            args.append(f"--profile-directory={settings.EDGE_PROFILE}")
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.EDGE_USER_DATA),
            channel="msedge",
            headless=True,
            args=args,
            accept_downloads=True,
            downloads_path=str(settings.DOWNLOADS_DIR)
        )               

    def new_page(self):
        if self.context.pages:
            return self.context.pages[0]
        return self.context.new_page()

    def close(self):
        self.context.close()
        self.playwright.stop() 
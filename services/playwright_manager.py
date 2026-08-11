from playwright.sync_api import sync_playwright
from database import settings
class PlaywrightManager:

    def __init__(self):
        self.playwright = sync_playwright().start()
        print("CONTEXTO CREADO")

        args = []

        if settings.EDGE_PROFILE:
            args.append(f"--profile-directory={settings.EDGE_PROFILE}")
        self.context = (
            self.playwright
            .chromium
            .launch_persistent_context(
                user_data_dir=str(settings.EDGE_USER_DATA),
                channel="msedge",
                headless=False,
                args=args,
            )
        )

    def new_page(self):
        return self.context.new_page()

    def close(self):
        self.context.close()
        self.playwright.stop()
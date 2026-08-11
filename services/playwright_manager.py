import os
from playwright.sync_api import sync_playwright
from database import settings


class PlaywrightManager:

    def __init__(self):
        self.playwright = sync_playwright().start()
        print("CONTEXTO CREADO")

        args = []
        if settings.EDGE_PROFILE:
            args.append(f"--profile-directory={settings.EDGE_PROFILE}")

        headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() in {"1", "true", "yes", "on"}
        launch_kwargs = {
            "user_data_dir": str(settings.EDGE_USER_DATA),
            "headless": headless,
            "args": args,
        }

        try:
            self.context = (
                self.playwright
                .chromium
                .launch_persistent_context(
                    **launch_kwargs,
                    channel="msedge" if os.name == "nt" else None,
                )
            )
        except Exception:
            self.context = (
                self.playwright
                .chromium
                .launch_persistent_context(**launch_kwargs)
            )

    def new_page(self):
        return self.context.new_page()

    def close(self):
        self.context.close()
        self.playwright.stop()
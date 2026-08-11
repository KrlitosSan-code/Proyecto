import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DRIVER_PATH = BASE_DIR / "drivers" / "msedgedriver.exe"


def _resolve_edge_settings():
    explicit_user_data = os.getenv("EDGE_USER_DATA")
    if explicit_user_data:
        user_data = Path(explicit_user_data).expanduser()
    else:
        candidate = Path(r"C:\EdgeSeleniumProfile")
        if candidate.exists():
            user_data = candidate
        else:
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                user_data = Path(local_app_data) / "Microsoft" / "Edge" / "User Data"
            else:
                user_data = candidate

    profile = os.getenv("EDGE_PROFILE", "Default")
    return user_data, profile


EDGE_USER_DATA, EDGE_PROFILE = _resolve_edge_settings()
EDGE_USER_DATA.mkdir(parents=True, exist_ok=True)

DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", r"C:\descargas\certificados"))
DOWNLOADS_RECIBOS = Path(os.getenv("DOWNLOADS_RECIBOS", r"C:\descargas\recibos"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DRIVER_PATH = BASE_DIR / "drivers" / "msedgedriver.exe"


def _resolve_edge_settings():
    explicit_user_data = os.getenv("EDGE_USER_DATA")

    if explicit_user_data:
        user_data = Path(explicit_user_data).expanduser()
    else:
        # Usar un perfil de automación separado para evitar interferir con sesiones de Edge ya abiertas.
        user_data = Path(r"C:\edge_playwright")

    profile = os.getenv("EDGE_PROFILE", "Default")
    profile_path = user_data / profile
    if not profile_path.exists() or not profile_path.is_dir():
        # No forzamos un perfil existente; Playwright creará el directorio de perfil si falta.
        profile = None

    return user_data, profile


EDGE_USER_DATA, EDGE_PROFILE = _resolve_edge_settings()
EDGE_USER_DATA.mkdir(parents=True, exist_ok=True)

DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", r"C:\descargas\certificados"))
DOWNLOADS_RECIBOS = Path(os.getenv("DOWNLOADS_RECIBOS", r"C:\descargas\recibos"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
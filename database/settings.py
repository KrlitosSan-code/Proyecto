# settings.py
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

def _resolve_edge_settings():
    explicit_user_data = os.getenv("EDGE_USER_DATA")

    if explicit_user_data:
        user_data = Path(explicit_user_data).expanduser()
    else:
        user_data = Path(r"C:\Perfil")

    # Si se define EDGE_PROFILE usa ese valor, de lo contrario "Default"
    profile = os.getenv("EDGE_PROFILE", "Default")

    return user_data, profile


EDGE_USER_DATA, EDGE_PROFILE = _resolve_edge_settings()
EDGE_USER_DATA.mkdir(parents=True, exist_ok=True)

DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", r"C:\descargas\certificados"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_RECIBOS = Path(os.getenv("DOWNLOADS_RECIBOS", r"C:\descargas\recibos"))
DOWNLOADS_RECIBOS.mkdir(parents=True, exist_ok=True)
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

DRIVER_PATH = BASE_DIR / "drivers" / "msedgedriver.exe"


def _resolve_edge_settings():
    explicit_user_data = os.getenv("EDGE_USER_DATA")

    if explicit_user_data:
        user_data = Path(explicit_user_data).expanduser()
    else:
        user_data = Path("/tmp/edge_playwright") if os.name != "nt" else Path(r"C:\edge_playwright")

    profile = os.getenv("EDGE_PROFILE", "Default")
    profile_path = user_data / profile
    if not profile_path.exists() or not profile_path.is_dir():
        profile = None

    return user_data, profile


EDGE_USER_DATA, EDGE_PROFILE = _resolve_edge_settings()
EDGE_USER_DATA.mkdir(parents=True, exist_ok=True)

DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR") or ("/tmp/notaria/certificados" if os.name != "nt" else r"C:\descargas\certificados"))
DOWNLOADS_RECIBOS = Path(os.getenv("DOWNLOADS_RECIBOS") or ("/tmp/notaria/recibos" if os.name != "nt" else r"C:\descargas\recibos"))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_RECIBOS.mkdir(parents=True, exist_ok=True)
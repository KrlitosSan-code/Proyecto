# Proyecto Notaria

## Requisitos
- Python 3.11+
- Entorno virtual en .venv

## Instalar dependencias
```powershell
cd c:/Users/krlit/source/Proyecto_Notaria
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Ejecutar localmente
```powershell
cd c:/Users/krlit/source/Proyecto_Notaria
.\.venv\Scripts\Activate.ps1
uvicorn frontend.app:app --host 127.0.0.1 --port 8000 --reload
```

## Probar arranque
```powershell
cd c:/Users/krlit/source/Proyecto_Notaria
.\.venv\Scripts\python.exe -m pytest -q tests/test_app_smoke.py
```

## Variables de entorno opcionales
- SUPABASE_URL
- SUPABASE_KEY
- SUPABASE_SERVICE_ROLE_KEY
- DOWNLOADS_DIR
- DOWNLOADS_RECIBOS
- EDGE_USER_DATA
- EDGE_PROFILE

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
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

## Probar arranque
```powershell
cd c:/Users/krlit/source/Proyecto_Notaria
.\.venv\Scripts\python.exe -m pytest -q tests/test_app_smoke.py
```

## Errores comunes y soluciones

- `HTML_FILE no está definido`: se debe declarar la ruta del archivo HTML principal y apuntarlo a `static/notaria_app.html`.
- `escritura no está definida` y `acta_id no está definido`: revisar que los handlers de envío no queden con referencias a variables fuera de su alcance; el flujo correcto debe usar `WorkflowService` y parámetros del endpoint.
- Si VS Code sigue mostrando errores viejos, recarga la ventana de Python/Pylance o ejecuta:

```powershell
python -m py_compile app.py
```

- Para pruebas reales del proyecto:

```powershell
python -m pytest -q tests/test_app_smoke.py
```

## Variables de entorno opcionales
- SUPABASE_URL
- SUPABASE_KEY
- SUPABASE_SERVICE_ROLE_KEY
- DOWNLOADS_DIR
- DOWNLOADS_RECIBOS
- EDGE_USER_DATA
- EDGE_PROFILE

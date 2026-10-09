# Distribuidora Río Claro

Proyecto en desarrollo para recibir facturas de proveedores, facilitar su
revisión y, con autorización humana, sincronizarlas como borradores en Odoo
Community. El alcance completo también contempla consultas operativas de
inventario y analítica. Por ahora, el incremento local permite registrar y
revisar facturas manualmente; todavía no conecta Odoo ni recibe archivos.

El documento de contexto de trabajo se mantiene fuera de este repositorio.

## Preparar el entorno

Requiere Python 3.11 o posterior y PostgreSQL. En macOS/Linux, desde la raíz del
proyecto:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` localmente con la conexión a tu base PostgreSQL. No compartas ni
subas ese archivo. Crea el esquema ejecutando:

```bash
alembic upgrade head
```

Inicia la aplicación solo para uso local:

```bash
uvicorn app.main:app --reload --host 127.0.0.1
```

Abre <http://127.0.0.1:8000>. Si el puerto está ocupado, añade
`--port 8765` al comando y abre <http://127.0.0.1:8765>. Este incremento no
incluye autenticación; no lo expongas a Internet ni a una red compartida.

## Pruebas

Ejecuta las pruebas automatizadas y las verificaciones de estilo/formato con:

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Las pruebas usan una base SQLite temporal en memoria; la aplicación utiliza
PostgreSQL para su ejecución normal.
# Distribuidora Río Claro

Proyecto en desarrollo para recibir facturas de proveedores, facilitar su
revisión y, con autorización humana, sincronizarlas como borradores en Odoo
Community. El alcance completo también contempla consultas operativas de
inventario y analítica. Por ahora, el incremento local permite registrar y
revisar facturas manualmente y adjuntar el documento original; todavía no
conecta Odoo ni recibe facturas por correo/WhatsApp o procesa OCR.

El documento de contexto de trabajo se mantiene fuera de este repositorio.
Odoo Community 20.0 es el scaffold ERP previsto para compras, ventas e
inventario; la aplicación Río Claro debe complementar esos procesos y no
duplicarlos.

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

Cada factura nueva requiere un documento PDF, JPG/JPEG o PNG de hasta 10 MiB.
Los archivos se guardan localmente en `var/invoices/` con permisos privados;
puedes cambiar la ruta mediante `INVOICE_STORAGE_DIR` en `.env`. La carpeta se
ignora en Git y debe incluirse en tus respaldos locales si necesitas conservar
los originales. Las facturas históricas pueden no tener adjunto.

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

## Odoo Community local

La instancia de desarrollo usa Docker Compose, Odoo Community 20.0 y un
PostgreSQL 16 separado de la base de la aplicación, según la versión mínima
que Odoo 20.0 requiere. Los puertos solo se publican en `127.0.0.1`; no uses
esta configuración como despliegue de producción.

1. En `.env`, añade `ODOO_DB_PASSWORD` con una contraseña local fuerte que no
   coincida con `DATABASE_PASSWORD`.
2. Copia `odoo/odoo.conf.example` a `odoo/odoo.conf` y reemplaza el marcador
   `admin_passwd` por otra contraseña fuerte distinta. La plantilla permite
   que Odoo escuche dentro del contenedor para que Docker reenvíe el puerto,
   mientras el host solo lo publica en localhost. Ambos archivos locales con
   valores sensibles están excluidos de Git.
3. Comprueba la configuración y levanta los servicios:

   ```bash
   docker compose config -q
   docker compose up -d
   docker compose ps
   ```

4. Abre `http://127.0.0.1:<ODOO_HTTP_PORT>` (en esta computadora:
   <http://127.0.0.1:8079>), crea una base de desarrollo y configura México
   como país y MXN como moneda de la compañía. Usa la localización disponible
   en Odoo Community para apoyar los flujos contables y de facturación internos
   que necesite el negocio.
5. Detén los servicios con `docker compose down`. Esto conserva los volúmenes
   de datos. No uses `docker compose down -v` salvo que quieras borrar
   permanentemente la base y el filestore locales.

El alcance del proyecto excluye la validación o certificación de CFDI y
cualquier conexión con el SAT. Los PDFs de Odoo se usan para los procesos
internos contemplados en este proyecto; no se afirma que sean comprobantes
fiscales digitales timbrados. Odoo Community se integrará mediante su API; la
aplicación no debe conectarse directamente a la base de datos de Odoo.
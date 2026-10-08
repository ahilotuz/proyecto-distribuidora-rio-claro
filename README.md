# Distribuidora Río Claro

Proyecto en desarrollo para recibir facturas de proveedores, facilitar su
revisión y, con autorización humana, sincronizarlas como borradores en Odoo
Community. El alcance también contempla consultas operativas de inventario y
analítica.

La aplicación y sus integraciones aún no están implementadas. El documento de
contexto de trabajo se mantiene fuera de este repositorio.

## Preparar el entorno

Requiere Python 3.11 o posterior. En macOS/Linux, desde la raíz del proyecto:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Pruebas

Todavía no hay pruebas que validen funcionalidades de la distribuidora. Se
agregarán junto con la primera funcionalidad implementada.
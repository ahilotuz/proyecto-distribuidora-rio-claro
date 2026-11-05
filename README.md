# Distribuidora Río Claro

Proyecto que integra **Odoo Community 20.0** con servicios de IA de
Microsoft Azure para apoyar la recepción, revisión y consulta de facturas de
proveedores dentro de una empresa. Odoo es el sistema de registro; la IA clasifica o extrae
información para revisión humana.

> **Estado:** este proyecto es un prototipo educativo. No está listo para procesar
> datos empresariales reales. La clasificación y extracción pueden equivocarse;
> una persona debe revisar los resultados y crear las facturas como borradores.

## Qué incluye

- Un addon de Odoo que permite la recepción de facturas, extracción de datos,
  consultas asistidas con permisos y entrada por Telegram.
- Una Azure Function de screening de correo basada en Azure AI Language.
- Integración opcional con Azure AI Document Intelligence, Azure OpenAI y Azure
  AI Speech.
- Compose para desarrollo local y una configuración de despliegue de referencia
  con Caddy.

La Function de screening analiza texto y metadatos de adjuntos; **no** inspecciona
el contenido de los archivos. El conjunto de datos incluido es sintético y no
demuestra precisión en producción. No se conecta al SAT ni valida CFDI.

## Arquitectura

```mermaid
flowchart LR
    subgraph Canales["Canales de entrada"]
        Mail[Correo de proveedor]
        Telegram[Telegram]
        Visitor[Visitante del sitio]
    end

    subgraph Odoo["Odoo: lógica de negocio y sistema de registro"]
        Intake[Complemento de recepción y revisión]
        Assistant[Asistente de Telegram<br/>permisos antes de consultar]
        LiveChat[Chat del sitio<br/>contenido público únicamente]
        Query[Servicio de consulta Odoo<br/>ORM + alcance por contacto y compañía]
        ERP[(PostgreSQL<br/>acceso por Odoo)]
        Draft[Factura de proveedor<br/>borrador revisado]
    end

    subgraph Azure["Servicios opcionales de Microsoft Azure"]
        Function[Azure Function]
        Language[Azure AI Language]
        DI[Document Intelligence]
        AOAI[Azure OpenAI]
        Speech[Azure AI Speech]
    end

    Mail --> Intake
    Telegram --> Intake
    Intake <--> ERP
    Intake -. filtrado opcional .-> Function
    Function --> Language
    Intake -->|extracción iniciada por una persona| DI
    DI -->|campos y confianza| Intake
    Intake -->|corrección humana y creación| Draft
    Draft --> ERP
    Telegram --> Assistant
    Assistant -->|selección de herramienta| AOAI
    AOAI -->|herramienta y argumentos| Assistant
    Assistant -->|herramienta y argumentos| Query
    Query -->|lectura de solo lectura| ERP
    ERP -->|resultados autorizados| Query
    Query -->|filas acotadas| Assistant
    Assistant -. voz opcional .-> Speech
    Visitor --> LiveChat
    LiveChat -->|solo datos publicados| AOAI
    AOAI --> LiveChat
```

## Estructura

```text
azure/email-screening/                 Azure Function y pruebas
custom_addons/rioclaro_invoice_inbox/   complemento Odoo y pruebas
deploy/                                Compose de producción y Caddy
docs/                                  evidencias visuales del curso, sanitizadas
scripts/                               utilidades de operación
compose.yaml                           entorno local Odoo + PostgreSQL
```

## Inicio rápido: Odoo local

Requisitos: Docker Compose v2 y una copia local de los archivos de configuración.

```bash
cp .env.example .env
cp odoo/odoo.conf.example odoo/odoo.conf
```

Edita `.env` y `odoo/odoo.conf`: define contraseñas fuertes, distintas y solo
locales. Después:

```bash
docker compose config -q
docker compose up -d
docker compose ps
```

Abre <http://127.0.0.1:8079> (puerto configurado en `.env.example`). Los datos
persisten en volúmenes Docker; `docker compose down -v` los elimina.

Para habilitar integraciones de Azure o Telegram, configura solo las variables
que necesites en `.env` a partir de `.env.example`. No actives el polling de
Telegram en más de una base de datos para el mismo bot.

## Pruebas y validaciones

Pruebas de Azure email screening:

```bash
cd azure/email-screening
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python prepare_dataset.py --output /tmp/rioclaro-synthetic-dataset
```

Comprobación de sintaxis para el código Python del repositorio:

```bash
python -m compileall -q azure/email-screening custom_addons scripts
```

Las pruebas del complemento Odoo se ejecutan dentro de Odoo con PostgreSQL;
consulta [`guia.md`](./guia.md) y el
[README del complemento](./custom_addons/rioclaro_invoice_inbox/README.md).
GitHub Actions ejecuta las pruebas disponibles de screening y la validación del
conjunto sintético en cada pull request.

## Documentación

- [Guía para entender y probar las funcionalidades](./custom_addons/rioclaro_invoice_inbox/README.md)
- [Guía del curso y configuración](./guia.md)
- [Semántica del chatbot](./CHATBOT_SEMANTICS.md)
- [Arquitectura de Telegram, Odoo y Azure AI](./TELEGRAM_ODOO_AZURE_AI_ARCHITECTURE.md)
- [Azure email screening](./azure/email-screening/README.md)
- [Despliegue de referencia](./deploy/README.md)
- [Lista antes de publicar](./docs/PUBLICATION_CHECKLIST.md)

## Seguridad, datos y licencia

No uses datos personales, facturas reales ni credenciales productivas para
pruebas. Revisa [SECURITY.md](./SECURITY.md) antes de reportar un problema.
Consulta la lista de publicación para revisar el historial Git y la configuración
de GitHub antes de hacer público el repositorio.

Este repositorio aún no declara una licencia raíz. Hasta que sus autores elijan
y agreguen una, no se debe asumir permiso para reutilizar el contenido. El
complemento declara su propia licencia en el manifiesto.

# Distribuidora Río Claro

The project extends Odoo Community 20.0 with a custom Odoo application for
ingesting supplier invoices received by email or WhatsApp. Odoo remains the
scaffold and system of record for purchases, sales, inventory, and accounting.

The custom application will provide invoice intake, review, and traceability
within Odoo. It must not duplicate standard ERP workflows or connect directly
to Odoo's PostgreSQL database. CFDI validation and any connection to Mexico's
SAT are outside this project's scope.

## Local Odoo development

The development environment runs Odoo Community 20.0 with a separate
PostgreSQL 16 database using Docker Compose. Both services use persistent
volumes. Host ports are bound to `127.0.0.1`; this setup is for local
development, not production.

1. Set `ODOO_DB_PASSWORD` in `.env` to a strong local password.
2. Copy `odoo/odoo.conf.example` to `odoo/odoo.conf` and replace the
   `admin_passwd` placeholder with a different strong password. Both files
   containing local secrets are excluded from Git.
3. Validate the Compose configuration and start the services:

   ```bash
   docker compose config -q
   docker compose up -d
   docker compose ps
   ```

4. Open `http://127.0.0.1:${ODOO_HTTP_PORT}` (default: <http://127.0.0.1:8079>)
   and use the development database.
5. Stop the services with `docker compose stop` or `docker compose down`.
   These commands preserve data volumes. Do not use `docker compose down -v`
   unless you intend to permanently delete local database and filestore data.
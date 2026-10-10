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

## Supplier invoice email intake

The first custom add-on is `rioclaro_invoice_inbox`. Configure one Odoo
incoming mail server for each company mailbox and set **Create a New Record**
to **Supplier Invoice Email**. Emails fetched from these servers all appear in
the same intake queue, with the incoming server name recorded in **Received
In**. Odoo stores the original email and its attachments in the intake
record's chatter. Repeated emails with the same `Message-ID` do not create
duplicate intake records. Accountants can move records to review or reject
them. This initial increment does not extract invoice values or create vendor
bills automatically.

The add-on is mounted read-only from `custom_addons/` into the Odoo container.
To enable real email retrieval:

1. In **Settings → Technical → Email → Incoming Mail Servers**, create one
   IMAP server for each company mailbox. Use the mailbox address as the server
   name so intake records clearly identify the source.
2. For each server, select **Supplier Invoice Email** in **Create a New
   Record**, enter that mailbox's host, username, and password, and test the
   connection.
3. Save and use **Fetch Now** to test retrieval. Emails fetched from every
   configured mailbox will appear under **Invoice Intake → Supplier Emails**.
   The incoming server's name is shown in **Received In**.

Use a mailbox created for development. Mailbox credentials belong in Odoo's
settings, not in this repository.

The intake menu is available to users in the accounting group. Telegram and
WhatsApp ingestion, OCR, invoice-to-bill conversion, and production mail
configuration are later increments. Actual email retrieval requires working
incoming-mail server configurations; the local automated tests simulate
incoming messages and do not connect to external mailboxes.
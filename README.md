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

## Azure email screening foundation

The Azure Function in `azure/email-screening/` classifies incoming message
text and returns a route: `ignore`, `review`, or `inspect_attachments`. The
Odoo invoice inbox add-on can call this Function before creating an intake
record. Screening is disabled by default and must be explicitly configured
before any incoming-mail server is polled.

Incoming invoice records use the company configured by the
`rioclaro_invoice_inbox.company_id` system parameter. Set it to the numeric
database ID of the company that owns the configured invoice inbox; if unset,
Odoo falls back to the processing user's company. This matters because Odoo
filters intake records by the companies selected in the current session. This
setting applies to all incoming invoice mail; separate company-specific
mailboxes will need explicit per-mailbox company mapping.

For the local development test:

1. Start the local Function host on the Docker bridge gateway reachable by
   Odoo (for this Compose network, `172.21.0.1:7071`). Follow
   `azure/email-screening/README.md`; do not bind the development host to a
   public interface.
2. In Odoo **Settings → Technical → Parameters → System Parameters**, set:
   `rioclaro_invoice_inbox.screening_enabled` to `True` and
   `rioclaro_invoice_inbox.screening_url` to
   `http://172.21.0.1:7071/api/screen-email`.
3. Restart the Odoo container after changing `.env` or Compose configuration.
   The optional `RIOCLARO_SCREENING_FUNCTION_KEY` environment variable is for
   a hosted Function key; leave it unset for local Core Tools.
4. Test the Odoo-to-Function connection and invoice/non-invoice routing before
   enabling or using **Fetch Now** on an incoming-mail server.

Do not use **Fetch Now** while the Function is unavailable. Odoo may mark a
message as handled after a screening error; correct the connection and resend
that test message if this occurs.

**Fetch Now processes all unread mail on that server, not only the two test
messages.** Use a disposable test mailbox or make sure the configured inbox has
no other unread messages before running it.

Only messages classified as `supplier_invoice` with route
`inspect_attachments` are retained for the invoice-intake route. This screens
the email text plus whether an attachment exists. It does **not** read or
verify attachment contents, prove a document is an invoice, or extract invoice
data. The fictional training and holdout examples are for learning only;
their synthetic evaluation is not evidence of real-world accuracy. Do not use
this filter for production decisions without representative evaluation and
document-level validation.
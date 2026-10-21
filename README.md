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
them. Invoice values are extracted only when a reviewer clicks **Extract Invoice
Data** (see below), and vendor bills are always created as drafts by a reviewer.

### Azure Document Intelligence extraction

The **Extract Invoice Data** button sends the first PDF, JPEG, or PNG
attachment of an intake record to the Azure `prebuilt-invoice` model. It fills
the vendor RFC, invoice number and date, currency, subtotal, taxes, total, and
the extracted line items. If Azure fails, the status becomes **Extraction
Error**, the reason is posted in the chatter, and the email and attachments are
kept so the reviewer can retry or enter the data manually. **Create Vendor
Bill** builds a draft bill with one line per extracted line.

Set these variables in the ignored `.env` file (see `.env.example`); never
commit the key:

- `RIOCLARO_DOCUMENT_INTELLIGENCE_ENDPOINT`
- `RIOCLARO_DOCUMENT_INTELLIGENCE_KEY`

Upgrade an existing database after pulling these changes:
`odoo -d <database> -u rioclaro_invoice_inbox`.

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

The intake menu is available to users in the accounting group. WhatsApp
ingestion and production mail configuration are later increments. Actual email
retrieval requires working incoming-mail server configurations; the local
automated tests simulate incoming messages and do not connect to external
mailboxes.

### Telegram invoice intake

Suppliers or staff can send a PDF, JPEG, PNG, or photo to the Telegram bot. A
scheduled action (every minute) polls the bot, creates one intake record per
file (`Received In` shows **Telegram**), saves the file as an attachment, and
replies "Recibido". Repeated deliveries of the same message do not create
duplicates. Use **Extract Invoice Data** on the record as with email.

1. Create a bot with @BotFather and set `RIOCLARO_TELEGRAM_BOT_TOKEN` in the
   ignored `.env` file (see `.env.example`). Never commit the token.
2. As an administrator, open **Invoice Intake → Telegram Contacts** and create
   one contact per authorized sender: the numeric Telegram user ID (shown by
   @userinfobot), a role (supplier, internal user, or customer), and a partner
   for suppliers and customers. The role preloads default permissions, which
   you can grant or revoke one by one (inventory, purchases, sales, billing,
   order status, quotation, submit vendor invoice, issue customer invoice).
   Deactivate a contact to revoke all access. Access is denied by default:
   unknown senders and senders without the **Submit vendor invoice**
   permission get "No autorizado." and nothing is stored.
3. Set `rioclaro_invoice_inbox.telegram_polling_enabled` to `True` in the one
   database that should poll the bot. Polling is off by default because a bot
   token must be polled by a single database only; otherwise databases
   compete for the same messages.
4. Authorized users can send natural-language questions in a private chat.
   Every private text message is treated as a read-only query. The assistant
   uses the contact's query permissions and partner/company scope, and each
   contact is limited to 5 requests per rolling 5-minute window. Group-chat
   messages are ignored.
5. Administrators can inspect **Invoice Intake → Assisted Query Audit**.
   The audit records Telegram user ID, selected tool, outcome, and message ID;
   it deliberately does not store the question or answer.

This MVP uses polling and the contact ACL only. A webhook with signature
verification and WhatsApp are later increments.

### Assisted text queries (Azure OpenAI tool-picker foundation)

The add-on includes a first safe assistant layer
(`rioclaro.assisted.query.ai`) that:

- asks Azure OpenAI to choose exactly one fixed read-only tool
  (`stock_by_product`, `search_products`, `get_product_stock`,
  `recent_purchases`, `recent_sales`, `recent_invoices`, `order_status`).
  `search_products` returns up to 5 candidates (exact SKU first, then all
  words in name/SKU) without quantities; `get_product_stock` takes a product
  ID (optionally `id,warehouse_id`) and returns labelled on-hand, free and
  forecast quantities, re-checked against the contact's company;
  `aggregate_sales`, `aggregate_purchases` and `aggregate_invoices` return
  Odoo-computed totals per currency for a bounded period (empty = last 30
  days, `<days>` up to 366, or `YYYY-MM-DD,YYYY-MM-DD`), counting only
  confirmed orders and posted invoices, with credit notes subtracted;
- executes that tool through Odoo ACL and partner/company scoping; and
- asks Azure OpenAI to phrase the final answer using only the authorized rows.

Set these environment variables in the ignored `.env` file:

- `RIOCLARO_AOAI_ENDPOINT`
- `RIOCLARO_AOAI_API_KEY`
- `RIOCLARO_AOAI_DEPLOYMENT`
- `RIOCLARO_SPEECH_ENDPOINT`
- `RIOCLARO_SPEECH_REGION`
- `RIOCLARO_SPEECH_KEY`

Telegram text handling, audit logging, and rate limiting are implemented.
Authorized users may also send a private Telegram voice note (OGG/Opus).
Voice notes are limited to 60 seconds. Azure AI Speech transcribes them in
Spanish (Mexico), and the same permission-scoped
read-only query flow handles the transcription, and the answer is sent as an
MP3 voice message. If speech synthesis or audio delivery fails, the bot sends
the answer as text instead. Voice requests use the same 5-per-contact,
per-5-minute limit and are marked as voice input in the audit view; audio and
transcribed text are not stored by the audit model.

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
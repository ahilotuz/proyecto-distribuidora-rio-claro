# Azure email screening foundation

This Azure Function accepts an email body and attachment-presence flag, calls
an Azure AI Language custom single-label classification deployment through
the official Azure Text Analytics SDK, and returns one of:

- `ignore`: a high-confidence `not_invoice` classification.
- `review`: low-confidence, unknown, or invoice-like email without attachments.
- `inspect_attachments`: a high-confidence `supplier_invoice` with attachments.

The Odoo invoice inbox add-on can call this endpoint before creating an intake
record. It retains high-confidence `supplier_invoice`/`inspect_attachments`
emails and `review` emails in the intake queue. Review emails require a human
to decide whether they are invoices; extraction and bill creation remain
manual. As a conservative fallback, an attached email with explicit invoice
cues in its subject, body, or attachment filename is routed to `review` even
if the classifier says `ignore`. High-confidence `not_invoice`/`ignore`
messages without invoice cues are excluded. This is not attachment inspection:
the Function does not download or verify files, confirm an invoice document,
or extract invoice data. Treat the result as an email-screening practice
integration only, not production invoice validation.

## Classifier setup

Create an Azure AI Language custom text classification project with these exact
single-label classes:

- `supplier_invoice`
- `not_invoice`

Train and deploy the model using representative, sanitized email-body examples
in Spanish and any other languages present in the monitored inboxes. Include
both invoices and unrelated mail (receipts, notifications, personal mail,
marketing, and routine correspondence). Evaluate false positives and false
negatives before enabling automatic routing. The default confidence threshold
is `0.90`; it is a conservative starting setting, not a guarantee of accuracy.

Assign the Function App's managed identity the Azure AI Language resource's
**Cognitive Services User** role. Configure `LANGUAGE_ENDPOINT`,
`LANGUAGE_PROJECT_NAME`, and `LANGUAGE_DEPLOYMENT_NAME` as Function App settings.
Set `INVOICE_CONFIDENCE_THRESHOLD` only after reviewing evaluation results.
Function keys must be stored in a Logic Apps secure parameter or Key Vault;
never commit them or mailbox credentials.

For local development, sign in with `az login` and grant that user the
**Cognitive Services User** role on the Language resource. The local settings
must point to the resource endpoint, project `supplier-invoice-email`, and
deployment `practice-screening-deployment`. The Function uses
`DefaultAzureCredential` locally and the Function App's managed identity when
hosted in Azure.

## Synthetic practice dataset

`data/synthetic_examples.jsonl` contains 120 fictional Spanish email bodies:
50 training and 10 holdout examples for each class. The examples contain no
real company or personal data. They are for learning the Azure import and
evaluation workflow only; because they are synthetic and intentionally simple,
their metrics do not demonstrate production accuracy.

Validate the labels and export one `.txt` file per example before uploading:

```bash
python prepare_dataset.py \
  --output build/synthetic-language-dataset
```

The output contains separate `train/` and `test/` directories plus a
`manifest.csv` mapping filenames to their labels. Upload the training documents
to the Azure Language project's connected storage and apply labels using this
manifest. Keep the holdout test documents separate from training and use them
only for evaluation. Do not enable automatic mailbox routing based on this
synthetic dataset.

## Provider workflow

Use separate Azure Logic Apps triggers/connectors for Microsoft 365 and Gmail.
Each trigger should read the email body and attachment-presence metadata and
call the Function's `screen-email` endpoint with:

```json
{
  "body": "Plain-text email body",
  "hasAttachments": true,
  "language": "es"
}
```

Initially configure all three routes as non-destructive test branches that log
message IDs only (not email bodies or attachments). Once evaluated, the direct
Odoo IMAP integration handles them as follows:

1. `ignore`: leave the message in its original inbox; do not send it to Odoo
   unless it has an attachment and an explicit invoice cue, in which case Odoo
   routes it to manual review.
2. `review`: create an intake record for a person to review; do not extract
   data or create a bill automatically.
3. `inspect_attachments`: create an intake record with the original email and
   attachments. A person reviews it and starts extraction manually.

For an initial safe rollout, monitor the invoice and review branches without
moving or deleting source messages. If using a separate Logic Apps workflow,
disable direct Odoo IMAP polling on general-purpose mailboxes to avoid having
both systems process the same messages.

## Local checks

Install the declared dependencies, then run the unit tests and dataset
validator:

```bash
cd azure/email-screening
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python prepare_dataset.py --output build/synthetic-language-dataset
```

The HTTP Function itself additionally requires packages in `requirements.txt`
and Azure Functions Core Tools. `local.settings.example.json` contains
placeholders only; copy it to `local.settings.json` for local development and
do not commit the latter. Running the Functions host locally also needs
Azure Functions Core Tools and a local storage emulator (Azurite), unless
`AzureWebJobsStorage` is configured to use an existing development storage
account.

To run the Function locally, use two terminals from this directory. Start
Azurite in the first terminal:

```bash
npm exec --yes --package=azurite -- azurite \
  --silent \
  --location /tmp/rioclaro-azurite \
  --blobHost 127.0.0.1 \
  --queueHost 127.0.0.1 \
  --tableHost 127.0.0.1
```

In the second terminal, install Python dependencies, create local settings from
the example, sign in to Azure CLI, and start the Functions host:

```bash
python -m pip install -r requirements.txt
cp local.settings.example.json local.settings.json
az login
npm exec --yes --package=azure-functions-core-tools@4 -- func start --port 7071
```

For the local Odoo bridge, bind the host only to the Docker gateway address
instead of localhost or all interfaces. Find the gateway address for the Odoo
container's Compose network, then use that address below (`172.21.0.1` in the
current local setup):

```bash
npm exec --yes --package=azure-functions-core-tools@4 -- \
  func start --port 7071 --address 172.21.0.1
```

Configure Odoo system parameters
`rioclaro_invoice_inbox.screening_enabled=True` and
`rioclaro_invoice_inbox.screening_url=http://172.21.0.1:7071/api/screen-email`.
Keep incoming-mail polling disabled until a test request from inside the Odoo
container succeeds. Local Core Tools does not require a Function key; for a
hosted Function, pass `RIOCLARO_SCREENING_FUNCTION_KEY` to the Odoo container
through a secret environment setting, not an Odoo system parameter or a
committed file.

The local settings file points to the practice deployment and must remain
uncommitted. To exercise the HTTP route, send a synthetic request:

```bash
curl -X POST http://127.0.0.1:7071/api/screen-email \
  -H 'Content-Type: application/json' \
  -d '{"body":"Su compra fue aprobada y estamos preparando el envío.","hasAttachments":false,"language":"es"}'
```

The local user's identity needs the **Cognitive Services User** role on the
Language resource. The Function endpoint returns a routing decision only; it
does not integrate with Odoo or any email provider.

Classifier training, cloud resources, provider connectors, attachment
inspection, and Odoo forwarding are not provisioned by this foundation.

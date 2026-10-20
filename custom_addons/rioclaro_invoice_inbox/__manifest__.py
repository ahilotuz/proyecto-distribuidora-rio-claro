{
    "name": "Río Claro Invoice Inbox",
    "summary": "Receive supplier invoice emails in Odoo for review",
    "author": "Distribuidora Río Claro",
    "version": "20.0.1.1.0",
    "category": "Accounting",
    "license": "LGPL-3",
    "depends": ["account", "mail"],
    "data": [
        "security/ir.access.csv",
        "data/mail_alias.xml",
        "data/ir_cron_telegram.xml",
        "views/invoice_intake_views.xml",
    ],
    "installable": True,
    "application": True,
}

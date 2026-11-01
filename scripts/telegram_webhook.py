"""Register, inspect or remove the Telegram webhook (run in the Odoo shell).

Usage (production, from deploy/):
    docker compose -f compose.prod.yaml exec -T -e ACTION=set odoo \
      odoo shell -c /etc/odoo/odoo.conf -d rioclaro < ../scripts/telegram_webhook.py

ACTION is ``set`` (default), ``info`` or ``delete``. Reads the bot token and
secret from the container environment and never prints them.
"""

import os

from odoo.addons.rioclaro_invoice_inbox.models.telegram_client import TelegramClient

action = os.environ.get("ACTION", "set")
client = TelegramClient(os.environ["RIOCLARO_TELEGRAM_BOT_TOKEN"])
base_url = os.environ.get("WEBHOOK_BASE_URL", "https://rioclaro.ropech.com")

if action == "set":
    client.set_webhook(
        f"{base_url}/rioclaro/telegram/webhook",
        os.environ["RIOCLARO_TELEGRAM_WEBHOOK_SECRET"],
    )
elif action == "delete":
    client.delete_webhook()
elif action != "info":
    raise SystemExit("ACTION must be set, info or delete")

info = client.webhook_info()
print(
    "WEBHOOK",
    {k: info.get(k) for k in ("url", "pending_update_count", "last_error_message")},
)

"""Durable queue of Telegram webhook updates with idempotent intake."""

from __future__ import annotations

import json
import logging
import os

from psycopg2 import IntegrityError

from odoo import api, fields, models

from .telegram_client import TelegramClient, TelegramError

_logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BATCH_SIZE = 20


class TelegramUpdate(models.Model):
    """One received Telegram update; the payload is erased once processed."""

    _name = "rioclaro.telegram.update"
    _description = "Telegram Webhook Update"
    _order = "id"

    update_id = fields.Integer(required=True, index=True)
    payload = fields.Text(help="Raw update JSON; cleared after processing.")
    state = fields.Selection(
        [("pending", "Pending"), ("done", "Done"), ("failed", "Failed")],
        default="pending",
        required=True,
        index=True,
    )
    attempts = fields.Integer(default=0)

    _update_id_unique = models.Constraint(
        "UNIQUE(update_id)", "This Telegram update was already received."
    )

    @api.model
    def enqueue(self, update: dict) -> bool:
        """Store an update once and wake the processing cron.

        Returns:
            ``True`` when it was queued, ``False`` for a duplicate delivery.
        """
        update_id = update["update_id"]
        if self.search_count([("update_id", "=", update_id)]):
            return False
        try:
            with self.env.cr.savepoint():
                self.create(
                    {"update_id": update_id, "payload": json.dumps(update)}
                )
        except IntegrityError:
            return False
        cron = self.env.ref(
            "rioclaro_invoice_inbox.ir_cron_process_telegram_updates",
            raise_if_not_found=False,
        )
        if cron:
            cron._trigger()
        return True

    @api.model
    def cron_process_pending(self) -> int:
        """Process queued updates through the shared Telegram message handler.

        Failed updates are retried up to ``MAX_ATTEMPTS`` times, then marked
        ``failed``. Payloads are erased as soon as an update is finished.

        Returns:
            The number of updates finished in this run.
        """
        token = os.environ.get("RIOCLARO_TELEGRAM_BOT_TOKEN", "")
        if not token:
            return 0
        client = TelegramClient(token)
        intake = self.env["rioclaro.invoice.intake"]
        finished = 0
        for record in self.search([("state", "=", "pending")], limit=BATCH_SIZE):
            message = TelegramClient.parse_update(json.loads(record.payload or "{}"))
            try:
                if message:
                    with self.env.cr.savepoint():
                        intake._handle_telegram_message(client, message)
            except TelegramError:
                record.attempts += 1
                _logger.warning(
                    "Telegram update %s failed (attempt %s).",
                    record.update_id,
                    record.attempts,
                )
                if record.attempts >= MAX_ATTEMPTS:
                    record.write({"state": "failed", "payload": False})
                continue
            record.write({"state": "done", "payload": False})
            finished += 1
        return finished

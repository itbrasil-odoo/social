# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import time
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Replies older than this are not correlated through the provider Message-ID.
RESOLVE_WINDOW = timedelta(days=30)
RESOLVE_MAX_ATTEMPTS = 6
# Resend allows a few API requests per second per team.
RESOLVE_REQUEST_INTERVAL = 0.5


class MailResendDelivery(models.Model):
    """One SMTP submission of a threaded email to Resend.

    Resend (Amazon SES) replaces the Message-ID on delivery and the SMTP reply
    only returns the Resend email id. When a recipient answers the From
    address instead of the reply address, the reply only quotes that new
    Message-ID, so it is fetched from the API and kept here to find the
    thread.
    """

    _name = "mail.resend.delivery"
    _description = "Resend Delivery"
    _order = "id desc"

    route_id = fields.Many2one(
        "mail.resend.route", required=True, ondelete="cascade", index=True
    )
    account_id = fields.Many2one(
        "mail.resend.account", required=True, ondelete="cascade", index=True
    )
    resend_email_id = fields.Char(required=True, index=True, copy=False)
    provider_message_id = fields.Char(
        index=True,
        copy=False,
        help="Message-ID set by Resend on delivery, quoted by replies in "
        "In-Reply-To and References.",
    )
    recipient_emails = fields.Char(
        copy=False, help="Normalized recipient addresses, separated by spaces."
    )
    resolve_attempts = fields.Integer(copy=False)

    _account_email_uniq = models.Constraint(
        "unique(account_id, resend_email_id)",
        "Resend email must be unique per Resend account.",
    )

    @api.model
    def _pending_domain(self):
        return [
            ("provider_message_id", "=", False),
            ("resolve_attempts", "<", RESOLVE_MAX_ATTEMPTS),
            ("create_date", ">=", fields.Datetime.now() - RESOLVE_WINDOW),
        ]

    def _resolve_provider_message_id(self):
        """Fetches the delivered Message-ID from the Resend API."""
        for index, delivery in enumerate(self):
            if index:
                time.sleep(RESOLVE_REQUEST_INTERVAL)
            values = {"resolve_attempts": delivery.resolve_attempts + 1}
            try:
                data = delivery.account_id._resend_request(
                    "GET", f"/emails/{delivery.resend_email_id}"
                )
            except UserError as err:
                # Rate limit or a temporary error: the next run tries again.
                _logger.info(
                    "Resend email %s: Message-ID not fetched: %s",
                    delivery.resend_email_id,
                    err,
                )
                delivery.write(values)
                break
            if data.get("message_id"):
                values["provider_message_id"] = data["message_id"]
            delivery.write(values)
        return self.filtered("provider_message_id")

    @api.model
    def _cron_resolve_provider_message_ids(self, limit=50):
        self.search(
            self._pending_domain(), limit=limit, order="id asc"
        )._resolve_provider_message_id()

    @api.model
    def _find_route(self, account, provider_message_ids, sender_email=False):
        """Route of the delivery whose Message-ID a reply quotes.

        Deliveries to the sender of the reply that were not resolved yet (the
        reply came before the cron) are resolved on the spot.
        """
        if not provider_message_ids:
            return self.env["mail.resend.route"]
        base_domain = [("account_id", "=", account.id)]
        delivery = self.search(
            base_domain + [("provider_message_id", "in", provider_message_ids)],
            limit=1,
        )
        if not delivery and sender_email:
            pending = self.search(
                base_domain
                + self._pending_domain()
                + [("recipient_emails", "ilike", sender_email)],
                limit=5,
            ).filtered(lambda d: sender_email in (d.recipient_emails or "").split())
            delivery = pending._resolve_provider_message_id().filtered(
                lambda d: d.provider_message_id in provider_message_ids
            )[:1]
        return delivery.route_id

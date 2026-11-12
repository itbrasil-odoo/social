# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

from .mail_tracking_email import RESEND_TRACKING_EVENT_TYPES


class MailResendAccount(models.Model):
    _inherit = "mail.resend.account"

    def _resend_webhook_event_types(self):
        return super()._resend_webhook_event_types() + list(RESEND_TRACKING_EVENT_TYPES)

    def _resend_process_delivery_event(self, payload, webhook_id=False):
        if payload.get("type") in RESEND_TRACKING_EVENT_TYPES:
            return (
                self.env["mail.tracking.email"]
                .sudo()
                ._resend_event_process(payload, webhook_id)
            )
        return super()._resend_process_delivery_event(payload, webhook_id)

# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class MailTrackingEvent(models.Model):
    _inherit = "mail.tracking.event"

    _sql_constraints = [
        (
            "resend_event_id_unique",
            "UNIQUE(resend_event_id)",
            "Resend event IDs must be unique!",
        )
    ]

    resend_event_id = fields.Char(
        string="Resend Event ID",
        copy=False,
        readonly=True,
        index=True,
        help="Svix message id of the Resend webhook that created this event.",
    )

    def _process_data(self, tracking_email, metadata, event_type, state):
        res = super()._process_data(tracking_email, metadata, event_type, state)
        res.update({"resend_event_id": metadata.get("resend_event_id", False)})
        return res

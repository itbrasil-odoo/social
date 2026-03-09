# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class MailResendInbound(models.Model):
    _name = "mail.resend.inbound"
    _description = "Resend Inbound Email"
    _order = "id desc"

    account_id = fields.Many2one(
        "mail.resend.account", required=True, ondelete="cascade", index=True
    )
    resend_email_id = fields.Char(required=True, index=True, copy=False)
    event_type = fields.Char()
    message_id = fields.Char(index=True)
    payload = fields.Text()
    state = fields.Selection(
        [
            ("processing", "Processing"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        default="processing",
        readonly=True,
        copy=False,
    )
    error_message = fields.Text(readonly=True, copy=False)
    mail_message_id = fields.Many2one("mail.message", readonly=True, copy=False)
    target_model = fields.Char(readonly=True, copy=False)
    target_res_id = fields.Integer(readonly=True, copy=False)

    _sql_constraints = [
        (
            "mail_resend_inbound_account_email_uniq",
            "unique(account_id, resend_email_id)",
            "Received email must be unique per Resend account.",
        )
    ]

    def _process(self):
        for inbound in self:
            try:
                email_data = inbound.account_id._retrieve_received_email(
                    inbound.resend_email_id
                )
                raw_bytes = inbound.account_id._download_raw_email(email_data)
                raw_bytes = raw_bytes or inbound.account_id._build_received_email_bytes(
                    email_data
                )
                allowed_company_ids = inbound.account_id.company_ids.ids
                inbound.env["mail.thread"].with_context(
                    allowed_company_ids=allowed_company_ids or inbound.env.companies.ids
                ).sudo().message_process(
                    None,
                    raw_bytes,
                    save_original=True,
                )
                mail_message = (
                    inbound.env["mail.message"]
                    .sudo()
                    .search(
                        [("message_id", "=", email_data["message_id"])],
                        limit=1,
                        order="id desc",
                    )
                )
                inbound.write(
                    {
                        "state": "done",
                        "error_message": False,
                        "mail_message_id": mail_message.id,
                        "target_model": mail_message.model,
                        "target_res_id": mail_message.res_id,
                        "message_id": email_data["message_id"],
                    }
                )
            except Exception as err:  # pylint: disable=broad-except
                _logger.exception(
                    "Failed to process Resend inbound email %s", inbound.resend_email_id
                )
                inbound.write(
                    {
                        "state": "error",
                        "error_message": str(err),
                    }
                )
                raise

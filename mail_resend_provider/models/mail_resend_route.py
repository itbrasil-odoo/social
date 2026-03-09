# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class MailResendRoute(models.Model):
    _name = "mail.resend.route"
    _description = "Resend Thread Route"
    _order = "id desc"

    mail_message_id = fields.Many2one(
        "mail.message", required=True, ondelete="cascade", index=True
    )
    mail_mail_id = fields.Many2one(
        "mail.mail", ondelete="set null", copy=False, index=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, ondelete="cascade", index=True
    )
    account_id = fields.Many2one(
        "mail.resend.account", required=True, ondelete="cascade", index=True
    )
    model = fields.Char(required=True, index=True)
    res_id = fields.Integer(required=True, index=True)
    reply_token = fields.Char(copy=False, index=True)
    reply_address = fields.Char(copy=False, index=True)
    provider_message_id = fields.Char(copy=False, index=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Sent"),
            ("matched", "Matched"),
        ],
        default="draft",
        readonly=True,
        copy=False,
    )

    _sql_constraints = [
        (
            "mail_resend_route_mail_message_uniq",
            "unique(mail_message_id)",
            "A Resend route already exists for this message.",
        ),
        (
            "mail_resend_route_reply_token_uniq",
            "unique(reply_token)",
            "Reply token must be unique.",
        ),
    ]

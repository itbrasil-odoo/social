# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    resend_account_id = fields.Many2one(
        "mail.resend.account",
        string="Resend Account",
        related="company_id.resend_account_id",
        readonly=False,
    )
    resend_outgoing_server_id = fields.Many2one(
        "ir.mail_server",
        string="Managed Resend Server",
        related="company_id.resend_outgoing_server_id",
        readonly=True,
    )
    resend_webhook_status = fields.Selection(
        related="resend_account_id.webhook_status",
        readonly=True,
    )
    resend_webhook_url = fields.Char(
        related="resend_account_id.webhook_url",
        readonly=True,
    )
    resend_last_sync_error = fields.Text(
        related="resend_account_id.last_sync_error",
        readonly=True,
    )

    def action_sync_resend_webhook(self):
        self.ensure_one()
        if not self.resend_account_id:
            raise UserError(_("Please configure a Resend account first."))
        self.company_id._ensure_resend_outgoing_server()
        result = self.resend_account_id.action_sync_webhook()
        if isinstance(result, dict):
            return result
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def action_open_resend_accounts(self):
        return self.env["ir.actions.actions"]._for_xml_id(
            "mail_resend_provider.mail_resend_account_action"
        )

# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    resend_account_id = fields.Many2one(
        "mail.resend.account",
        string="Resend Account",
        ondelete="set null",
    )
    resend_outgoing_server_id = fields.Many2one(
        "ir.mail_server",
        string="Resend Outgoing Server",
        readonly=True,
        ondelete="set null",
        copy=False,
    )

    def write(self, values):
        tracked_fields = {"resend_account_id"}
        before_accounts = {company.id: company.resend_account_id for company in self}
        result = super().write(values)
        if tracked_fields & set(values):
            for company in self:
                if company.resend_account_id:
                    company._ensure_resend_outgoing_server()
                elif company.resend_outgoing_server_id:
                    company._disable_resend_outgoing_server(before_accounts[company.id])
        return result

    def _disable_resend_outgoing_server(self, previous_account):
        self.ensure_one()
        server = self.resend_outgoing_server_id
        if not server:
            server = (
                self.env["ir.mail_server"]
                .sudo()
                .search(
                    [
                        ("resend_company_id", "=", self.id),
                        ("resend_managed", "=", True),
                    ],
                    limit=1,
                )
            )
        if server:
            server.active = False
            server.name = _("%s (disabled)", server.name)
        self.resend_outgoing_server_id = False
        if previous_account and previous_account.company_ids:
            previous_account.company_ids._ensure_resend_outgoing_server()

    def _ensure_resend_outgoing_server(self):
        servers = self.env["ir.mail_server"]
        for company in self:
            if not company.resend_account_id:
                continue
            account = company.resend_account_id
            server = company.resend_outgoing_server_id
            if not server:
                server = (
                    self.env["ir.mail_server"]
                    .sudo()
                    .search(
                        [
                            ("resend_company_id", "=", company.id),
                            ("resend_managed", "=", True),
                        ],
                        limit=1,
                    )
                )
            values = {
                "name": _("Resend SMTP - %s", company.display_name),
                "smtp_authentication": "resend",
                "smtp_host": "smtp.resend.com",
                "smtp_port": 587,
                "smtp_encryption": "starttls",
                "smtp_user": "resend",
                "smtp_pass": account.api_key,
                "from_filter": False,
                "active": True,
                "resend_managed": True,
                "resend_account_id": account.id,
                "resend_company_id": company.id,
            }
            if server:
                server.sudo().write(values)
            else:
                server = self.env["ir.mail_server"].sudo().create(values)
            if company.resend_outgoing_server_id != server:
                company.resend_outgoing_server_id = server
            servers |= server
        return servers

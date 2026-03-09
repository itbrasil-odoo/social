# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models, tools


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def _resend_parse_external_email_identity(self, value):
        if not value:
            return False, False
        display_name, parsed_email = tools.parse_contact_from_email(value)
        normalized_email = tools.email_normalize(parsed_email, strict=False)
        return display_name or False, normalized_email or False

    def _resend_get_email_identity_fix_values(self):
        self.ensure_one()
        if self.user_ids or not self.email or not self.email_normalized:
            return {}

        display_name, normalized_email = self._resend_parse_external_email_identity(
            self.email
        )
        if not normalized_email or normalized_email != self.email_normalized:
            return {}

        values = {}
        if self.email != normalized_email:
            values["email"] = normalized_email
        if display_name and (not self.name or self.name == self.email):
            values["name"] = display_name
        return values

    def _resend_sanitize_external_email_identity(self):
        for partner in self:
            values = partner._resend_get_email_identity_fix_values()
            if values:
                partner.sudo().write(values)
        return self

    @api.model
    def _resend_repair_external_email_identities(self):
        partners = self.with_context(active_test=False).search(
            [
                ("email", "!=", False),
                ("email", "like", "%<%"),
                ("user_ids", "=", False),
            ]
        )
        partners._resend_sanitize_external_email_identity()
        return partners

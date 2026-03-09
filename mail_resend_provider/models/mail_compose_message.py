# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    resend_original_email_from = fields.Char(copy=False)

    @api.depends("template_id", "record_company_id")
    def _compute_mail_server_id(self):
        result = super()._compute_mail_server_id()
        for composer in self.filtered(lambda wizard: not wizard.template_id):
            company = composer.record_company_id or composer.env.company
            composer.mail_server_id = company.resend_outgoing_server_id
        return result

    @api.depends(
        "composition_mode",
        "email_from",
        "model",
        "res_domain",
        "res_ids",
        "template_id",
        "mail_server_id",
        "record_company_id",
    )
    def _compute_authorship(self):
        result = super()._compute_authorship()
        mail_mail = self.env["mail.mail"]
        for composer in self:
            current_email_from = composer.email_from
            company = composer.record_company_id or composer.env.company
            mail_server = composer.mail_server_id or company.resend_outgoing_server_id
            values = mail_mail._resend_prepare_sender_values(
                email_from=current_email_from,
                reply_to=composer.reply_to,
                company=company,
                mail_server=mail_server,
            )
            if values.get("email_from"):
                composer.resend_original_email_from = current_email_from
                composer.email_from = values["email_from"]
            else:
                if not (
                    mail_server.resend_managed
                    and composer.resend_original_email_from
                    and current_email_from != composer.resend_original_email_from
                ):
                    composer.resend_original_email_from = current_email_from
        return result

    def _prepare_mail_values(self, res_ids):
        mail_values_all = super()._prepare_mail_values(res_ids)
        self.ensure_one()
        original_email_from_values = self._resend_get_original_email_from_values(
            res_ids
        )
        mail_mail = self.env["mail.mail"]
        for res_id, mail_values in mail_values_all.items():
            company = self.env["res.company"].browse(mail_values["record_company_id"])
            mail_server = self.mail_server_id or company.resend_outgoing_server_id
            original_email_from = original_email_from_values.get(res_id)
            prepared_values = mail_mail._resend_prepare_sender_values(
                email_from=original_email_from,
                reply_to=mail_values.get("reply_to"),
                company=company,
                mail_server=mail_server,
            )
            if not prepared_values:
                continue
            previous_email_from = mail_values.get("email_from")
            mail_values.update(prepared_values)
            if (
                original_email_from
                and mail_values.get("reply_to") == previous_email_from
                and previous_email_from != original_email_from
            ):
                mail_values["reply_to"] = original_email_from
        return mail_values_all

    def _resend_get_original_email_from_values(self, res_ids):
        self.ensure_one()
        if self.template_id and self.template_id.email_from:
            rendered = self._generate_template_for_composer(
                res_ids,
                ["email_from"],
                find_or_create_partners=False,
            )
            fallback_email_from = (
                self.resend_original_email_from
                or self.env.context.get("default_email_from")
                or self.env.user.email_formatted
            )
            return {
                res_id: rendered.get(res_id, {}).get("email_from")
                or fallback_email_from
                for res_id in res_ids
            }
        fallback_email_from = (
            self.resend_original_email_from
            or self.env.context.get("default_email_from")
            or self.env.user.email_formatted
        )
        return {res_id: fallback_email_from for res_id in res_ids}

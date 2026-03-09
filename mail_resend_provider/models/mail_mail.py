# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, models, tools
from odoo.exceptions import UserError


class MailMail(models.Model):
    _inherit = "mail.mail"

    @api.model_create_multi
    def create(self, values_list):
        for values in values_list:
            company = self._resend_resolve_company_from_values(values)
            if (
                not values.get("mail_server_id")
                and company
                and company.resend_account_id
            ):
                values["mail_server_id"] = company._ensure_resend_outgoing_server().id
            server = self._resend_resolve_server_from_values(values, company=company)
            values.update(
                self._resend_prepare_sender_values(
                    email_from=values.get("email_from"),
                    reply_to=values.get("reply_to"),
                    company=company,
                    mail_server=server,
                )
            )
        return super().create(values_list)

    def _split_by_mail_configuration(self):
        self.filtered(
            lambda mail: not mail.mail_server_id and mail._resend_get_company()
        )._assign_resend_outgoing_server()
        return super()._split_by_mail_configuration()

    def send(self, auto_commit=False, raise_exception=False, post_send_callback=None):
        self.filtered(
            lambda mail: mail.state == "outgoing"
        )._resend_prepare_outgoing_mails()
        return super().send(
            auto_commit=auto_commit,
            raise_exception=raise_exception,
            post_send_callback=post_send_callback,
        )

    @api.model
    def _resend_resolve_company_from_values(self, values):
        company_id = values.get("record_company_id")
        if company_id:
            return self.env["res.company"].browse(company_id)
        mail_message_id = values.get("mail_message_id")
        if mail_message_id:
            return self.env["mail.message"].browse(mail_message_id).record_company_id
        return self.env.company

    def _resend_get_company(self):
        self.ensure_one()
        return (
            self.record_company_id
            or self.mail_message_id.record_company_id
            or self.env.company
        )

    @api.model
    def _resend_resolve_server_from_values(self, values, company=False):
        if values.get("mail_server_id"):
            return self.env["ir.mail_server"].browse(values["mail_server_id"])
        if company and company.resend_account_id:
            return company._ensure_resend_outgoing_server()
        return self.env["ir.mail_server"]

    @api.model
    def _resend_get_sender_company(self, company=False, mail_server=False):
        mail_server = mail_server.sudo() if mail_server else mail_server
        if mail_server and mail_server.resend_managed and mail_server.resend_company_id:
            return mail_server.resend_company_id
        return company

    @api.model
    def _resend_get_verified_sender_email(self, company):
        company = self._resend_get_sender_company(company=company)
        if not company:
            raise UserError(
                _("Unable to determine the company for the Resend outgoing email.")
            )
        sender_email = company.alias_domain_id.default_from_email
        if not sender_email:
            raise UserError(
                _(
                    "Company %(company)s must have an email alias domain with a "
                    "Default From email to send messages through Resend.",
                    company=company.display_name,
                )
            )
        return tools.email_normalize(sender_email, strict=False) or sender_email

    @api.model
    def _resend_format_email_from(self, email_from, verified_sender_email):
        verified_sender_email = (
            tools.email_normalize(verified_sender_email, strict=False)
            or verified_sender_email
        )
        normalized_email_from = tools.email_normalize(email_from, strict=False)
        if normalized_email_from == verified_sender_email:
            return email_from
        display_name = ""
        if email_from:
            display_name, _normalized_email = tools.parse_contact_from_email(email_from)
        if display_name:
            return tools.formataddr((display_name, verified_sender_email))
        return verified_sender_email

    @api.model
    def _resend_prepare_sender_values(
        self, email_from=False, reply_to=False, company=False, mail_server=False
    ):
        mail_server = mail_server.sudo() if mail_server else mail_server
        company = self._resend_get_sender_company(
            company=company,
            mail_server=mail_server,
        )
        if not mail_server or not mail_server.resend_managed:
            return {}
        verified_sender_email = self._resend_get_verified_sender_email(company)
        forced_email_from = self._resend_format_email_from(
            email_from,
            verified_sender_email,
        )
        if forced_email_from == email_from:
            return {}
        values = {"email_from": forced_email_from}
        if not reply_to and email_from:
            values["reply_to"] = email_from
        return values

    def _resend_prepare_outgoing_mails(self):
        for mail in self:
            if not mail.mail_server_id:
                company = mail._resend_get_company()
                if company and company.resend_account_id:
                    mail.mail_server_id = company._ensure_resend_outgoing_server()
            values = mail._resend_prepare_sender_values(
                email_from=mail.email_from,
                reply_to=mail.reply_to,
                company=mail._resend_get_company(),
                mail_server=mail.mail_server_id,
            )
            if values:
                mail.write(values)

    def _assign_resend_outgoing_server(self):
        for mail in self.filtered(lambda record: not record.mail_server_id):
            company = mail._resend_get_company()
            if company and company.resend_account_id:
                mail.mail_server_id = company._ensure_resend_outgoing_server()

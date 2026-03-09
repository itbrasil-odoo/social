# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError

from odoo.addons.base.models.ir_mail_server import MailDeliveryException


class IrMailServer(models.Model):
    _inherit = "ir.mail_server"

    smtp_authentication = fields.Selection(
        selection_add=[("resend", "Resend API Key")],
        ondelete={"resend": "set default"},
    )
    resend_managed = fields.Boolean(copy=False, readonly=True)
    resend_account_id = fields.Many2one(
        "mail.resend.account",
        string="Resend Account",
        ondelete="set null",
        readonly=True,
        copy=False,
    )
    resend_company_id = fields.Many2one(
        "res.company",
        string="Managed Company",
        ondelete="set null",
        readonly=True,
        copy=False,
    )

    @api.depends("smtp_authentication")
    def _compute_smtp_authentication_info(self):
        resend_servers = self.filtered(
            lambda server: server.smtp_authentication == "resend"
        )
        resend_servers.smtp_authentication_info = _(
            "Use Resend SMTP with an API key as the password. The managed "
            "configuration uses smtp.resend.com with STARTTLS on port 587."
        )
        return super(
            IrMailServer, self - resend_servers
        )._compute_smtp_authentication_info()

    @api.onchange("smtp_authentication")
    def _onchange_smtp_authentication_resend(self):
        resend_servers = self.filtered(
            lambda server: server.smtp_authentication == "resend"
        )
        resend_servers.smtp_host = "smtp.resend.com"
        resend_servers.smtp_encryption = "starttls"
        resend_servers.smtp_port = 587
        resend_servers.smtp_user = "resend"

    @api.onchange("smtp_encryption")
    def _onchange_encryption(self):
        if any(server.smtp_authentication == "resend" for server in self):
            resend_servers = self.filtered(
                lambda server: server.smtp_authentication == "resend"
            )
            resend_servers.smtp_port = 587
        return super(
            IrMailServer,
            self.filtered(lambda server: server.smtp_authentication != "resend"),
        )._onchange_encryption()

    @api.constrains(
        "smtp_authentication",
        "smtp_host",
        "smtp_encryption",
        "smtp_port",
        "smtp_user",
        "smtp_pass",
    )
    def _check_resend_configuration(self):
        resend_servers = self.filtered(
            lambda server: server.smtp_authentication == "resend"
        )
        for server in resend_servers:
            if server.smtp_host != "smtp.resend.com":
                raise UserError(
                    _(
                        'Incorrect SMTP server for Resend mail server "%s".',
                        server.name,
                    )
                )
            if server.smtp_encryption != "starttls":
                raise UserError(
                    _(
                        'Incorrect Connection Security for Resend mail server "%s". '
                        'Please set it to "TLS (STARTTLS)".',
                        server.name,
                    )
                )
            if server.smtp_port != 587:
                raise UserError(
                    _(
                        'Incorrect SMTP port for Resend mail server "%s". '
                        "Please set it to 587.",
                        server.name,
                    )
                )
            if server.smtp_user != "resend":
                raise UserError(
                    _(
                        'Incorrect username for Resend mail server "%s". '
                        'Please set it to "resend".',
                        server.name,
                    )
                )
            if not server.smtp_pass:
                raise UserError(
                    _(
                        "Please configure the Resend API key on mail server “%s”.",
                        server.name,
                    )
                )

    def send_email(
        self,
        message,
        mail_server_id=None,
        smtp_server=None,
        smtp_port=None,
        smtp_user=None,
        smtp_password=None,
        smtp_encryption=None,
        smtp_ssl_certificate=None,
        smtp_ssl_private_key=None,
        smtp_debug=False,
        smtp_session=None,
    ):
        mail_server = self.browse(mail_server_id) if mail_server_id else self[:1]
        try:
            return super().send_email(
                message,
                mail_server_id=mail_server_id,
                smtp_server=smtp_server,
                smtp_port=smtp_port,
                smtp_user=smtp_user,
                smtp_password=smtp_password,
                smtp_encryption=smtp_encryption,
                smtp_ssl_certificate=smtp_ssl_certificate,
                smtp_ssl_private_key=smtp_ssl_private_key,
                smtp_debug=smtp_debug,
                smtp_session=smtp_session,
            )
        except MailDeliveryException as err:
            translated_error = False
            if mail_server:
                translated_error = mail_server._resend_translate_delivery_error(
                    err,
                    message,
                )
            if translated_error:
                raise MailDeliveryException(
                    _("Mail Delivery Failed"),
                    translated_error,
                ) from err
            raise

    def _resend_translate_delivery_error(self, error, message):
        self.ensure_one()
        if not self.resend_managed:
            return False
        error_message = "\n".join(str(arg) for arg in error.args if arg)
        error_message_lower = error_message.lower()
        if not any(
            token in error_message_lower
            for token in (
                "domain is not verified",
                "domain mismatch",
                "add and verify your domain",
                "403-error-domain-mismatch",
            )
        ):
            return False
        sender_email = (
            tools.email_normalize(message["From"], strict=False) or message["From"]
        )
        sender_domain = tools.email_domain_extract(sender_email)
        if sender_domain:
            return _(
                "Resend rejected the email because the sender domain %(domain)s "
                "is not verified for the From address %(email_from)s. Verify "
                "this exact domain or subdomain in Resend Domains before "
                "sending: https://resend.com/domains",
                domain=sender_domain,
                email_from=sender_email,
            )
        return _(
            "Resend rejected the email because the sender address %(email_from)s "
            "does not use a verified domain. Verify the exact domain in Resend "
            "Domains before sending: https://resend.com/domains",
            email_from=sender_email,
        )

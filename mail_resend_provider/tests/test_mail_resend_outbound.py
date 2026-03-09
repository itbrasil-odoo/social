# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from email.message import EmailMessage
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import Form
from odoo.tests.common import tagged
from odoo.tools import formataddr

from odoo.addons.base.models.ir_mail_server import MailDeliveryException

from .common import MailResendProviderCommon


@tagged("-at_install", "post_install")
class TestMailResendOutbound(MailResendProviderCommon):
    def test_mail_create_rewrites_sender_to_company_alias_domain(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Company 2 Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
            }
        )
        self.assertEqual(
            mail.email_from,
            formataddr(
                (
                    "Employee C2",
                    self.company_2.alias_domain_id.default_from_email,
                )
            ),
        )
        self.assertEqual(mail.reply_to, "Employee C2 <employee@wrong-domain.test>")

    def test_mail_create_assigns_company_server(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Company 2 Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": f"{self.default_from}@{self.alias_domain}",
                "record_company_id": self.company_2.id,
            }
        )
        self.assertEqual(mail.mail_server_id, self.company_2.resend_outgoing_server_id)

    def test_mail_create_keeps_explicit_server(self):
        manual_server = self.env["ir.mail_server"].create(
            {
                "name": "Manual SMTP",
                "smtp_host": "smtp.example.com",
                "smtp_encryption": "none",
                "smtp_user": "manual",
                "smtp_pass": "manual",
            }
        )
        mail = self.env["mail.mail"].create(
            {
                "subject": "Manual Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": f"{self.default_from}@{self.alias_domain}",
                "record_company_id": self.company_2.id,
                "mail_server_id": manual_server.id,
            }
        )
        self.assertEqual(mail.mail_server_id, manual_server)

    def test_mail_create_keeps_explicit_reply_to(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Manual Reply To",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "reply_to": "support@example.com",
                "record_company_id": self.company_2.id,
            }
        )
        self.assertEqual(mail.reply_to, "support@example.com")

    def test_send_uses_explicit_company_server(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Send Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": f"{self.default_from}@{self.alias_domain}",
                "record_company_id": self.company_2.id,
            }
        )
        with self.mock_smtplib_connection():
            mail.send()
        self.assertEqual(
            self.connect_mocked.call_args.kwargs["mail_server_id"],
            self.company_2.resend_outgoing_server_id.id,
        )

    def test_send_rewrites_existing_outgoing_mail_before_smtp(self):
        original_email_from = "Support Agent <agent@wrong-domain.test>"
        expected_email_from = formataddr(
            (
                "Support Agent",
                self.company_2.alias_domain_id.default_from_email,
            )
        )
        mail = self.env["mail.mail"].create(
            {
                "subject": "Queued Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": f"{self.default_from}@{self.alias_domain}",
                "record_company_id": self.company_2.id,
            }
        )
        mail.write({"email_from": original_email_from, "reply_to": False})

        with self.mock_mail_gateway():
            mail.send()

        self.assertEqual(mail.email_from, expected_email_from)
        self.assertEqual(mail.reply_to, original_email_from)
        self.assertEqual(self._mails[0]["email_from"], expected_email_from)
        self.assertEqual(self._mails[0]["reply_to"], original_email_from)

    def test_mail_create_uses_company_specific_alias_domain(self):
        company_2_alias_domain = self.env["mail.alias.domain"].create(
            {
                "name": "company2.resend.test",
                "bounce_alias": "bounce.c2",
                "catchall_alias": "catchall.c2",
                "default_from": "notifications.c2",
            }
        )
        self.company_2.alias_domain_id = company_2_alias_domain

        mail = self.env["mail.mail"].create(
            {
                "subject": "Company 2 Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
            }
        )

        self.assertEqual(
            mail.email_from,
            formataddr(("Employee C2", company_2_alias_domain.default_from_email)),
        )

    def test_mail_create_blocks_resend_when_company_has_no_default_from(self):
        self.company_2.alias_domain_id = False
        with self.assertRaisesRegex(UserError, "Default From email"):
            self.env["mail.mail"].create(
                {
                    "subject": "Missing Alias",
                    "body_html": "<p>Hello</p>",
                    "email_to": "recipient@example.com",
                    "email_from": "Employee C2 <employee@wrong-domain.test>",
                    "record_company_id": self.company_2.id,
                }
            )

    def test_composer_defaults_company_server(self):
        form = Form(
            self.env["mail.compose.message"]
            .with_user(self.user_employee_c2)
            .with_context(
                default_model="res.partner",
                default_res_ids=self.partner_company_2.ids,
            )
        )
        self.assertEqual(
            form.mail_server_id,
            self.company_2.resend_outgoing_server_id,
        )

    def test_composer_defaults_verified_from(self):
        form = Form(
            self.env["mail.compose.message"]
            .with_user(self.user_employee_c2)
            .with_context(
                default_model="res.partner",
                default_res_ids=self.partner_company_2.ids,
            )
        )
        self.assertEqual(
            form.email_from,
            formataddr(
                (
                    self.user_employee_c2.partner_id.name,
                    self.company_2.alias_domain_id.default_from_email,
                )
            ),
        )

    def test_resend_delivery_error_is_translated(self):
        message = EmailMessage()
        message["From"] = formataddr(
            (
                "Support Agent",
                self.company_2.alias_domain_id.default_from_email,
            )
        )
        message["To"] = "recipient@example.com"
        message["Subject"] = "Resend failure"
        message["Message-Id"] = "<resend-failure@example.com>"
        message.set_content("Body")
        smtp_error = MailDeliveryException(
            "Mail Delivery Failed",
            (
                "Mail delivery failed via SMTP server 'None'.\n"
                "SMTPDataError: (550, b'The company2.resend.test domain is not "
                "verified. Please, add and verify your domain on "
                "https://resend.com/domains')"
            ),
        )

        with patch(
            "odoo.addons.base.models.ir_mail_server.IrMailServer.send_email",
            side_effect=smtp_error,
        ):
            with self.assertRaisesRegex(MailDeliveryException, "Resend rejected"):
                self.company_2.resend_outgoing_server_id.send_email(
                    message,
                    mail_server_id=self.company_2.resend_outgoing_server_id.id,
                )

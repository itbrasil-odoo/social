# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from email import message_from_bytes
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
    def test_partner_identity_repair_is_idempotent_and_skips_internal_users(self):
        malformed_value = '"Renan Teixeira" <contatorenanteixeira@icloud.com>'
        external_partner = self.env["res.partner"].create(
            {
                "name": malformed_value,
                "email": malformed_value,
            }
        )
        internal_partner = self.user_employee_c2.partner_id
        original_internal_values = {
            "name": internal_partner.name,
            "email": internal_partner.email,
        }
        internal_partner.write(
            {
                "name": malformed_value,
                "email": malformed_value,
            }
        )

        self.env["res.partner"]._resend_repair_external_email_identities()
        self.env["res.partner"]._resend_repair_external_email_identities()

        external_partner.invalidate_recordset()
        internal_partner.invalidate_recordset()
        self.assertEqual(external_partner.name, "Renan Teixeira")
        self.assertEqual(external_partner.email, "contatorenanteixeira@icloud.com")
        self.assertEqual(internal_partner.name, malformed_value)
        self.assertEqual(internal_partner.email, malformed_value)

        internal_partner.write(original_internal_values)

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

    def test_notification_keeps_message_server_and_sender(self):
        """A template-driven post (e.g. invoice "Send & Print") stores the
        server and sender on the message; the notification mail.mail only
        carries mail_message_id and must not reroute through Resend."""
        manual_server = self.env["ir.mail_server"].create(
            {
                "name": "Billing SMTP",
                "smtp_host": "smtp.example.com",
                "smtp_encryption": "none",
                "smtp_user": "billing",
                "smtp_pass": "billing",
            }
        )
        customer = self.env["res.partner"].create(
            {"name": "Customer", "email": "customer@example.com"}
        )
        message = customer.with_context(mail_notify_force_send=False).message_post(
            body="<p>Invoice</p>",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            partner_ids=customer.ids,
            email_from="Billing <billing@example.com>",
            mail_server_id=manual_server.id,
        )
        mail = (
            self.env["mail.mail"].sudo().search([("mail_message_id", "=", message.id)])
        )
        self.assertTrue(mail)
        self.assertEqual(mail.mail_server_id, manual_server)
        self.assertEqual(mail.email_from, "Billing <billing@example.com>")
        self.assertEqual(message.mail_server_id, manual_server)
        self.assertEqual(message.email_from, "Billing <billing@example.com>")
        self.assertFalse(
            self.env["mail.resend.route"].search([("mail_message_id", "=", message.id)])
        )

    def test_notification_without_server_uses_resend(self):
        customer = self.env["res.partner"].create(
            {"name": "Customer", "email": "customer@example.com"}
        )
        message = customer.with_context(mail_notify_force_send=False).message_post(
            body="<p>Hello</p>",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            partner_ids=customer.ids,
            email_from="Billing <billing@example.com>",
        )
        mail = (
            self.env["mail.mail"].sudo().search([("mail_message_id", "=", message.id)])
        )
        self.assertEqual(
            mail.mail_server_id, self.env.company.resend_outgoing_server_id
        )
        # The message of the thread keeps its sender; only the outgoing
        # headers carry the verified address.
        self.assertEqual(mail.email_from, "Billing <billing@example.com>")
        self.assertEqual(message.email_from, "Billing <billing@example.com>")
        sent = self._send_and_parse(mail)
        self.assertEqual(
            sent["From"],
            formataddr(
                ("Billing", self.env.company.alias_domain_id.default_from_email)
            ),
        )

    def test_incoming_email_keeps_sender_when_followers_are_notified(self):
        """A reply from a customer notified to the followers through Resend:
        the stored message must keep the customer address."""
        customer = self.env["res.partner"].create(
            {"name": "Customer", "email": "customer@example.com"}
        )
        follower = self.env["res.partner"].create(
            {"name": "Follower", "email": "follower@example.com"}
        )
        customer.message_subscribe(partner_ids=follower.ids)
        message = customer.with_context(mail_notify_force_send=False).message_post(
            body="<p>Received</p>",
            message_type="email",
            subtype_xmlid="mail.mt_comment",
            author_id=customer.id,
            email_from="Customer <customer@example.com>",
        )
        mail = (
            self.env["mail.mail"].sudo().search([("mail_message_id", "=", message.id)])
        )
        self.assertTrue(mail)
        sent = self._send_and_parse(mail)
        message.invalidate_recordset()
        self.assertEqual(message.email_from, "Customer <customer@example.com>")
        self.assertEqual(
            sent["From"],
            formataddr(
                ("Customer", self.env.company.alias_domain_id.default_from_email)
            ),
        )

    def _send_and_parse(self, mail):
        with self.mock_smtplib_connection():
            mail.send()
        self.assertEqual(self.testing_smtp_session.data.call_count, 1)
        raw = self.testing_smtp_session.data.call_args[0][0]
        return message_from_bytes(raw if isinstance(raw, bytes) else raw.encode())

    def _post_from(self, email_from):
        customer = self.env["res.partner"].create(
            {"name": "Customer", "email": "customer@example.com"}
        )
        message = customer.with_context(mail_notify_force_send=False).message_post(
            body="<p>Hello</p>",
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            partner_ids=customer.ids,
            email_from=email_from,
        )
        return (
            self.env["mail.mail"].sudo().search([("mail_message_id", "=", message.id)])
        )

    def test_sender_with_own_server_keeps_it(self):
        """A sender with its own outgoing server is not moved to Resend."""
        self.env["ir.mail_server"].create(
            {
                "name": "Own SMTP",
                "smtp_host": "smtp.own.example.org",
                "from_filter": "owner@own.example.org",
            }
        )
        mail = self._post_from("Owner <owner@own.example.org>")
        self.assertFalse(mail.mail_server_id.resend_managed)
        self.assertEqual(mail.email_from, "Owner <owner@own.example.org>")
        # Another sender of the same domain has no server: Resend.
        mail = self._post_from("Other <other@own.example.org>")
        self.assertEqual(
            mail.mail_server_id, self.env.company.resend_outgoing_server_id
        )

    def test_domain_server_does_not_take_senders_off_resend(self):
        """Only a server for the exact sender address keeps it; a server for a
        whole domain does not, so notifications still go through Resend."""
        self.env["ir.mail_server"].create(
            {
                "name": "Domain SMTP",
                "smtp_host": "smtp.domain.example.org",
                "from_filter": "domain.example.org",
            }
        )
        mail = self._post_from("Anyone <anyone@domain.example.org>")
        self.assertEqual(
            mail.mail_server_id, self.env.company.resend_outgoing_server_id
        )

    def test_account_sender_domain(self):
        """The sender domain of the account replaces the company alias domain
        for the Resend sender and the reply addresses, which keep working
        without changing the email alias domain of the company."""
        alias_domain = self.env.company.alias_domain_id.name
        self.resend_account.write(
            {"sender_domain": "mail.example.org", "sender_alias": "odoo"}
        )
        self.assertEqual(self.resend_account.sender_email, "odoo@mail.example.org")
        mail = self._post_from("Billing <billing@example.com>")
        self.assertEqual(
            mail.mail_server_id, self.env.company.resend_outgoing_server_id
        )
        sent = self._send_and_parse(mail)
        self.assertEqual(sent["From"], formataddr(("Billing", "odoo@mail.example.org")))
        reply = mail._resend_build_reply_address("abc", self.env.company)
        self.assertEqual(reply, "reply-abc@mail.example.org")
        self.assertEqual(self.env.company.alias_domain_id.name, alias_domain)

    def test_mail_create_keeps_explicit_reply_to(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Manual Reply To",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "reply_to": "support@example.com",
                "record_company_id": self.company_2.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
            }
        )
        route = self.env["mail.resend.route"].search(
            [("mail_message_id", "=", mail.mail_message_id.id)]
        )
        self.assertEqual(mail.reply_to, "support@example.com")
        self.assertEqual(route.mail_message_id, mail.mail_message_id)
        self.assertFalse(route.reply_token)
        self.assertFalse(route.reply_address)

    def test_threadable_mail_create_uses_technical_reply_to(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Threaded Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
            }
        )
        route = self.env["mail.resend.route"].search(
            [("mail_message_id", "=", mail.mail_message_id.id)]
        )
        expected_reply_to = self.env["mail.message"]._get_reply_to(
            {
                "email_from": mail.email_from,
                "message_type": mail.message_type,
                "model": mail.model,
                "res_id": mail.res_id,
            }
        )

        self.assertTrue(route)
        self.assertTrue(route.reply_token)
        self.assertEqual(
            route.reply_address,
            f"reply-{route.reply_token}@{self.company_2.alias_domain_id.name}",
        )
        self.assertEqual(
            mail.reply_to,
            self.env["mail.mail"]._resend_format_reply_to(
                expected_reply_to,
                route.reply_address,
            ),
        )
        self.assertEqual(mail.references, mail.mail_message_id.message_id)
        self.assertEqual(route.state, "draft")

    def test_threadable_mail_skips_route_when_reply_to_force_new(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Force New",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
                "reply_to_force_new": True,
            }
        )
        route = self.env["mail.resend.route"].search(
            [("mail_message_id", "=", mail.mail_message_id.id)]
        )
        self.assertFalse(route)

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

    def test_send_sanitizes_malformed_recipient_partner_before_smtp(self):
        malformed_value = '"Renan Teixeira" <contatorenanteixeira@icloud.com>'
        malformed_partner = self.env["res.partner"].create(
            {
                "name": malformed_value,
                "email": malformed_value,
            }
        )
        mail = self.env["mail.mail"].create(
            {
                "subject": "Malformed Recipient",
                "body_html": "<p>Hello</p>",
                "email_from": f"{self.default_from}@{self.alias_domain}",
                "record_company_id": self.company_2.id,
                "recipient_ids": [(4, malformed_partner.id)],
            }
        )
        malformed_partner.write(
            {
                "name": malformed_value,
                "email": malformed_value,
            }
        )

        with self.mock_smtplib_connection():
            mail.send()

        malformed_partner.invalidate_recordset()
        self.assertEqual(malformed_partner.name, "Renan Teixeira")
        self.assertEqual(malformed_partner.email, "contatorenanteixeira@icloud.com")
        self.assertEqual(
            self.testing_smtp_session.rcpt.call_args_list[0].args[0],
            "contatorenanteixeira@icloud.com",
        )

    def test_prepare_outgoing_merges_canonical_reference_on_threadable_mail(self):
        original_reference = "<upstream-thread@example.com>"
        mail = self.env["mail.mail"].create(
            {
                "subject": "Queued Threaded Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": f"{self.default_from}@{self.alias_domain}",
                "record_company_id": self.company_2.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
            }
        )
        mail.write({"references": original_reference})
        mail._resend_prepare_outgoing_mails()

        self.assertEqual(
            mail.references,
            f"{original_reference} {mail.mail_message_id.message_id}",
        )

    def test_send_captures_provider_message_id(self):
        provider_message_id = "<provider-thread@example.com>"
        mail = self.env["mail.mail"].create(
            {
                "subject": "Provider Thread",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
            }
        )
        route = self.env["mail.resend.route"].search(
            [("mail_message_id", "=", mail.mail_message_id.id)]
        )

        with self.mock_smtplib_connection(
            data_reply=(250, f"Ok {provider_message_id}".encode())
        ):
            mail.send()

        route.invalidate_recordset()
        self.assertEqual(mail.message_id, provider_message_id)
        self.assertEqual(route.provider_message_id, provider_message_id)
        self.assertEqual(route.state, "sent")

    def test_send_records_resend_delivery(self):
        """The Resend SMTP reply only carries the Resend email id: it is kept
        with the route, and the mail keeps the Odoo Message-ID."""
        resend_email_id = "01a1cd8b-3cd1-783c-9015-8bf57e8820d9"
        mail = self.env["mail.mail"].create(
            {
                "subject": "Delivery",
                "body_html": "<p>Hello</p>",
                "email_to": "Recipient <Recipient@Example.com>",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
            }
        )
        odoo_message_id = mail.message_id
        route = self.env["mail.resend.route"].search(
            [("mail_message_id", "=", mail.mail_message_id.id)]
        )
        with self.mock_smtplib_connection(data_reply=(250, resend_email_id.encode())):
            mail.send()
        delivery = self.env["mail.resend.delivery"].search(
            [("route_id", "=", route.id)]
        )
        self.assertEqual(delivery.resend_email_id, resend_email_id)
        self.assertEqual(delivery.recipient_emails, "recipient@example.com")
        self.assertFalse(delivery.provider_message_id)
        self.assertEqual(mail.message_id, odoo_message_id)

        ses_message_id = "<0103-ses@sa-east-1.amazonses.com>"
        with patch(
            "odoo.addons.mail_resend_provider.models.mail_resend_account."
            "MailResendAccount._resend_request",
            return_value={"id": resend_email_id, "message_id": ses_message_id},
        ):
            self.env["mail.resend.delivery"]._cron_resolve_provider_message_ids()
        self.assertEqual(delivery.provider_message_id, ses_message_id)
        self.assertEqual(delivery.resolve_attempts, 1)

    def test_send_without_route_records_no_delivery(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "No thread",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
            }
        )
        with self.mock_smtplib_connection(
            data_reply=(250, b"01a1cd8b-3cd1-783c-9015-8bf57e8820d9")
        ):
            mail.send()
        self.assertFalse(self.env["mail.resend.delivery"].search([]))

    def test_send_auto_delete_mail_does_not_crash_after_unlink(self):
        mail = self.env["mail.mail"].create(
            {
                "subject": "Auto Delete",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": "Employee C2 <employee@wrong-domain.test>",
                "record_company_id": self.company_2.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
                "auto_delete": True,
            }
        )

        with self.mock_smtplib_connection():
            mail.send()

        self.assertFalse(self.env["mail.mail"].browse(mail.id).exists())
        self.assertEqual(self.testing_smtp_session.data.call_count, 1)

    def test_non_resend_server_keeps_existing_references(self):
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
                "subject": "Manual Threaded Mail",
                "body_html": "<p>Hello</p>",
                "email_to": "recipient@example.com",
                "email_from": f"{self.default_from}@{self.alias_domain}",
                "record_company_id": self.company_2.id,
                "mail_server_id": manual_server.id,
                "model": "res.partner",
                "res_id": self.partner_company_2.id,
                "message_type": "email",
                "references": "<manual-reference@example.com>",
            }
        )

        self.assertEqual(mail.references, "<manual-reference@example.com>")

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
            "odoo.addons.mail_resend_provider.models.ir_mail_server."
            "IrMailServer._resend_send_email",
            side_effect=smtp_error,
        ):
            with self.assertRaisesRegex(MailDeliveryException, "Resend rejected"):
                self.company_2.resend_outgoing_server_id.send_email(
                    message,
                    mail_server_id=self.company_2.resend_outgoing_server_id.id,
                )

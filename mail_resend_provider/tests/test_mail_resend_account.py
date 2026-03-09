# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from email import message_from_bytes
from unittest.mock import patch

from odoo.tests.common import tagged
from odoo.tools import mute_logger

from .common import FakeJsonResponse, MailResendProviderCommon


@tagged("-at_install", "post_install")
class TestMailResendAccount(MailResendProviderCommon):
    def test_outgoing_server_provisioning(self):
        self.assertTrue(self.company_admin.resend_outgoing_server_id)
        self.assertTrue(self.company_2.resend_outgoing_server_id)
        self.assertNotEqual(
            self.company_admin.resend_outgoing_server_id,
            self.company_2.resend_outgoing_server_id,
        )
        for server in (
            self.company_admin.resend_outgoing_server_id
            + self.company_2.resend_outgoing_server_id
        ):
            self.assertEqual(server.smtp_authentication, "resend")
            self.assertEqual(server.smtp_host, "smtp.resend.com")
            self.assertEqual(server.smtp_port, 587)
            self.assertEqual(server.smtp_encryption, "starttls")
            self.assertEqual(server.smtp_user, "resend")
            self.assertEqual(server.smtp_pass, "re_test_shared")
            self.assertTrue(server.resend_managed)
            self.assertEqual(server.resend_account_id, self.resend_account)

    def test_outgoing_server_updates_on_api_key_change(self):
        self.resend_account.write({"api_key": "re_test_updated"})
        self.assertEqual(
            self.company_admin.resend_outgoing_server_id.smtp_pass, "re_test_updated"
        )
        self.assertEqual(
            self.company_2.resend_outgoing_server_id.smtp_pass, "re_test_updated"
        )

    def test_build_received_email_bytes_fetches_attachments(self):
        email_data = {
            "id": "re_email_001",
            "from": "Sender <sender@example.com>",
            "to": [
                f"{self.partner_alias_admin.alias_name}@{self.mail_alias_domain.name}"
            ],
            "cc": [],
            "bcc": [],
            "reply_to": [],
            "subject": "Attachment Subject",
            "message_id": "<attachment-message@example.com>",
            "headers": {"X-Test": "yes"},
            "text": "Attachment body",
            "html": False,
        }
        with (
            patch.object(
                type(self.resend_account),
                "_list_received_attachments",
                autospec=True,
                return_value=[
                    {
                        "download_url": "https://download.example.com/file",
                        "filename": "invoice.txt",
                        "content_type": "text/plain",
                    }
                ],
            ),
            patch.object(
                type(self.resend_account),
                "_download_content",
                autospec=True,
                return_value=b"invoice-content",
            ),
        ):
            email_bytes = self.resend_account._build_received_email_bytes(email_data)
        parsed = message_from_bytes(email_bytes)
        self.assertEqual(parsed["Message-Id"], "<attachment-message@example.com>")
        attachment = next(part for part in parsed.walk() if part.get_filename())
        self.assertEqual(attachment.get_filename(), "invoice.txt")

    def test_webhook_sync_create(self):
        account = self.env["mail.resend.account"].create(
            {
                "name": "Resend Create",
                "api_key": "re_test_create",
            }
        )
        with patch(
            "odoo.addons.mail_resend_provider.models.mail_resend_account.requests.request"
        ) as request_mock:
            request_mock.side_effect = [
                FakeJsonResponse({"object": "list", "has_more": False, "data": []}),
                FakeJsonResponse(
                    {
                        "object": "webhook",
                        "id": "wh_123",
                        "signing_secret": "whsec_created",
                    }
                ),
            ]
            account.action_sync_webhook()
        self.assertEqual(account.webhook_id, "wh_123")
        self.assertEqual(account.webhook_signing_secret, "whsec_created")
        self.assertEqual(account.webhook_status, "synced")
        self.assertEqual(request_mock.call_count, 2)

    def test_webhook_sync_reuses_matching_endpoint(self):
        account = self.env["mail.resend.account"].create(
            {
                "name": "Resend Reuse",
                "api_key": "re_test_reuse",
                "webhook_signing_secret": "whsec_existing",
            }
        )
        with patch(
            "odoo.addons.mail_resend_provider.models.mail_resend_account.requests.request"
        ) as request_mock:
            request_mock.side_effect = [
                FakeJsonResponse(
                    {
                        "object": "list",
                        "has_more": False,
                        "data": [
                            {
                                "id": "wh_existing",
                                "endpoint": account.webhook_url,
                                "events": ["email.received"],
                                "status": "disabled",
                            }
                        ],
                    }
                ),
                FakeJsonResponse({"object": "webhook", "id": "wh_existing"}),
            ]
            account.action_sync_webhook()
        self.assertEqual(account.webhook_id, "wh_existing")
        self.assertEqual(account.webhook_status, "synced")
        self.assertEqual(request_mock.call_args_list[1].args[0], "PATCH")

    def test_webhook_sync_updates_existing_webhook_id(self):
        self.resend_account.write({"webhook_id": "wh_force_update"})
        with patch(
            "odoo.addons.mail_resend_provider.models.mail_resend_account.requests.request",
            return_value=FakeJsonResponse(
                {"object": "webhook", "id": "wh_force_update"}
            ),
        ) as request_mock:
            self.resend_account.action_sync_webhook()
        self.assertEqual(request_mock.call_count, 1)
        self.assertEqual(request_mock.call_args.args[0], "PATCH")

    @mute_logger("odoo.http")
    def test_webhook_sync_stores_last_error(self):
        with patch(
            "odoo.addons.mail_resend_provider.models.mail_resend_account.requests.request",
            return_value=FakeJsonResponse({"message": "boom"}, status_code=400),
        ):
            result = self.resend_account.action_sync_webhook()
        self.assertEqual(self.resend_account.webhook_status, "error")
        self.assertIn("boom", self.resend_account.last_sync_error)
        self.assertEqual(result["tag"], "display_notification")

    def test_webhook_sync_requires_https_base_url(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "http://odoo.test.example.com"
        )
        with patch(
            "odoo.addons.mail_resend_provider.models.mail_resend_account.requests.request"
        ) as request_mock:
            result = self.resend_account.action_sync_webhook()
        self.assertEqual(self.resend_account.webhook_status, "error")
        self.assertIn("HTTPS webhook URL", self.resend_account.last_sync_error)
        self.assertEqual(result["tag"], "display_notification")
        request_mock.assert_not_called()

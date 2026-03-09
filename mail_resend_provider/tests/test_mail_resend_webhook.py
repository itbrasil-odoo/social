# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from unittest.mock import patch

from odoo.tests.common import HttpCase, tagged
from odoo.tools import mute_logger

from .common import MailResendProviderCommon


@tagged("-at_install", "post_install")
class TestMailResendWebhook(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.test.example.com"
        )
        cls.user_admin = cls.env.ref("base.user_admin")
        cls.company_admin = cls.user_admin.company_id
        cls.company_2 = cls.env["res.company"].create(
            {
                "name": "Webhook Company 2",
                "currency_id": cls.env.ref("base.USD").id,
                "email": "webhook-company2@example.com",
            }
        )
        cls.user_admin.write({"company_ids": [(4, cls.company_2.id)]})
        cls.alias_domain = cls.env["mail.alias.domain"].create(
            {
                "name": "resendwebhook.test",
                "bounce_alias": "bounce-http",
                "catchall_alias": "catchall-http",
                "default_from": "notifications-http",
            }
        )
        cls.company_admin.alias_domain_id = cls.alias_domain
        cls.company_2.alias_domain_id = cls.alias_domain
        cls.account = cls.env["mail.resend.account"].create(
            {
                "name": "Webhook Account",
                "api_key": "re_http_test",
                "webhook_signing_secret": "whsec_http_test",
            }
        )
        cls.company_admin.resend_account_id = cls.account.id
        cls.company_2.resend_account_id = cls.account.id
        model_id = cls.env["ir.model"]._get_id("res.partner")
        cls.alias_admin = (
            cls.env["mail.alias"]
            .sudo()
            .create(
                {
                    "alias_name": "http-admin",
                    "alias_model_id": model_id,
                    "alias_defaults": f"{{'company_id': {cls.company_admin.id}}}",
                    "alias_domain_id": cls.alias_domain.id,
                }
            )
        )
        cls.alias_company_2 = (
            cls.env["mail.alias"]
            .sudo()
            .create(
                {
                    "alias_name": "http-company2",
                    "alias_model_id": model_id,
                    "alias_defaults": f"{{'company_id': {cls.company_2.id}}}",
                    "alias_domain_id": cls.alias_domain.id,
                }
            )
        )

    def _post_webhook(self, payload, headers, token=None):
        return self.url_open(
            f"/mail_resend_provider/webhook/{token or self.account.webhook_token}",
            data=json.dumps(payload),
            headers=headers,
        )

    def test_webhook_unknown_token(self):
        response = self._post_webhook(
            {"type": "email.received", "data": {"email_id": "unknown"}},
            {"Content-Type": "application/json"},
            token="unknown-token",
        )
        self.assertEqual(response.status_code, 404)

    @mute_logger("odoo.addons.mail_resend_provider.controllers.main")
    def test_webhook_invalid_signature(self):
        response = self._post_webhook(
            {"type": "email.received", "data": {"email_id": "invalid-signature"}},
            {
                "Content-Type": "application/json",
                "svix-id": "msg_invalid",
                "svix-timestamp": "1",
                "svix-signature": "invalid",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            self.env["mail.resend.inbound"].search(
                [("resend_email_id", "=", "invalid-signature")]
            )
        )

    def test_webhook_creates_record_from_alias(self):
        message_id = "<resend-http-1@example.com>"
        recipient = f"{self.alias_admin.alias_name}@{self.alias_domain.name}"
        payload = {
            "type": "email.received",
            "data": {
                "email_id": "email_http_1",
                "message_id": message_id,
            },
        }
        raw_email = MailResendProviderCommon.make_raw_email(
            to_address=recipient,
            subject="Webhook Partner",
            message_id=message_id,
            body="Body 1",
        )
        headers = MailResendProviderCommon.make_webhook_headers(
            json.dumps(payload), self.account.webhook_signing_secret
        )
        with (
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._retrieve_received_email",
                autospec=True,
                return_value={
                    "id": "email_http_1",
                    "message_id": message_id,
                    "raw": {"download_url": "https://download/raw-1"},
                },
            ),
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._download_raw_email",
                autospec=True,
                return_value=raw_email,
            ),
        ):
            response = self._post_webhook(payload, headers)
        self.assertEqual(response.status_code, 200)
        partner = self.env["res.partner"].search([("name", "=", "Webhook Partner")])
        self.assertEqual(len(partner), 1)
        self.assertEqual(partner.company_id, self.company_admin)
        inbound = self.env["mail.resend.inbound"].search(
            [("resend_email_id", "=", "email_http_1")]
        )
        self.assertEqual(inbound.state, "done")
        self.assertEqual(inbound.mail_message_id.model, "res.partner")
        self.assertEqual(inbound.mail_message_id.res_id, partner.id)

    def test_webhook_reply_updates_existing_thread(self):
        first_message_id = "<resend-http-2@example.com>"
        second_message_id = "<resend-http-3@example.com>"
        recipient = f"{self.alias_company_2.alias_name}@{self.alias_domain.name}"

        first_payload = {
            "type": "email.received",
            "data": {
                "email_id": "email_http_2",
                "message_id": first_message_id,
            },
        }
        first_headers = MailResendProviderCommon.make_webhook_headers(
            json.dumps(first_payload),
            self.account.webhook_signing_secret,
            msg_id="msg_first",
        )
        first_email = MailResendProviderCommon.make_raw_email(
            to_address=recipient,
            subject="Reply Target",
            message_id=first_message_id,
            body="Body first",
        )
        with (
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._retrieve_received_email",
                autospec=True,
                return_value={
                    "id": "email_http_2",
                    "message_id": first_message_id,
                    "raw": {"download_url": "https://download/raw-2"},
                },
            ),
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._download_raw_email",
                autospec=True,
                return_value=first_email,
            ),
        ):
            self._post_webhook(first_payload, first_headers)

        partner = self.env["res.partner"].search(
            [("name", "=", "Reply Target")], limit=1
        )
        self.assertTrue(partner)

        second_payload = {
            "type": "email.received",
            "data": {
                "email_id": "email_http_3",
                "message_id": second_message_id,
            },
        }
        second_headers = MailResendProviderCommon.make_webhook_headers(
            json.dumps(second_payload),
            self.account.webhook_signing_secret,
            msg_id="msg_second",
        )
        second_email = MailResendProviderCommon.make_raw_email(
            to_address=recipient,
            subject="Re: Reply Target",
            message_id=second_message_id,
            body="Body second",
            in_reply_to=first_message_id,
            references=first_message_id,
        )
        with (
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._retrieve_received_email",
                autospec=True,
                return_value={
                    "id": "email_http_3",
                    "message_id": second_message_id,
                    "raw": {"download_url": "https://download/raw-3"},
                },
            ),
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._download_raw_email",
                autospec=True,
                return_value=second_email,
            ),
        ):
            response = self._post_webhook(second_payload, second_headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.env["mail.message"].search_count(
                [("model", "=", "res.partner"), ("res_id", "=", partner.id)]
            ),
            2,
        )
        self.assertEqual(
            self.env["res.partner"].search_count([("name", "=", "Reply Target")]), 1
        )

    def test_webhook_imports_attachment_and_ignores_replay(self):
        message_id = "<resend-http-attachment@example.com>"
        recipient = f"{self.alias_admin.alias_name}@{self.alias_domain.name}"
        payload = {
            "type": "email.received",
            "data": {
                "email_id": "email_http_attachment",
                "message_id": message_id,
            },
        }
        headers = MailResendProviderCommon.make_webhook_headers(
            json.dumps(payload),
            self.account.webhook_signing_secret,
            msg_id="msg_attachment",
        )
        raw_email = MailResendProviderCommon.make_raw_email(
            to_address=recipient,
            subject="Attachment Replay",
            message_id=message_id,
            body="Attachment body",
            attachments=[("ticket.txt", b"attachment-data", "text/plain")],
        )
        with (
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._retrieve_received_email",
                autospec=True,
                return_value={
                    "id": "email_http_attachment",
                    "message_id": message_id,
                    "raw": {"download_url": "https://download/raw-attachment"},
                },
            ),
            patch(
                "odoo.addons.mail_resend_provider.models.mail_resend_account.MailResendAccount._download_raw_email",
                autospec=True,
                return_value=raw_email,
            ),
        ):
            first_response = self._post_webhook(payload, headers)
            second_response = self._post_webhook(payload, headers)

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        inbound = self.env["mail.resend.inbound"].search(
            [("resend_email_id", "=", "email_http_attachment")]
        )
        self.assertEqual(len(inbound), 1)
        self.assertEqual(inbound.state, "done")
        self.assertEqual(
            sorted(inbound.mail_message_id.attachment_ids.mapped("name")),
            ["original_email.eml", "ticket.txt"],
        )

# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timezone
from email.message import EmailMessage

from requests import HTTPError
from svix.webhooks import Webhook

from odoo.addons.mail.tests.common import MailCommon


class FakeJsonResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = str(self._payload)
        self.content = b"{}"

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(response=self)

    def json(self):
        return self._payload


class MailResendProviderCommon(MailCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://odoo.test.example.com"
        )
        cls.mail_alias_domain.company_ids = [(4, cls.company_2.id)]
        cls.company_2.alias_domain_id = cls.mail_alias_domain
        cls.resend_account = cls.env["mail.resend.account"].create(
            {
                "name": "Shared Resend",
                "api_key": "re_test_shared",
                "webhook_signing_secret": "whsec_test_shared",
            }
        )
        cls.company_admin.resend_account_id = cls.resend_account.id
        cls.company_2.resend_account_id = cls.resend_account.id
        cls.partner_company_2 = cls.env["res.partner"].create(
            {
                "name": "Company 2 Contact",
                "company_id": cls.company_2.id,
                "email": "company2-contact@example.com",
            }
        )
        cls.partner_alias_model_id = cls.env["ir.model"]._get_id("res.partner")
        cls.partner_alias_admin = (
            cls.env["mail.alias"]
            .sudo()
            .create(
                {
                    "alias_name": "resend-admin",
                    "alias_model_id": cls.partner_alias_model_id,
                    "alias_defaults": f"{{'company_id': {cls.company_admin.id}}}",
                    "alias_domain_id": cls.mail_alias_domain.id,
                }
            )
        )
        cls.partner_alias_company_2 = (
            cls.env["mail.alias"]
            .sudo()
            .create(
                {
                    "alias_name": "resend-company2",
                    "alias_model_id": cls.partner_alias_model_id,
                    "alias_defaults": f"{{'company_id': {cls.company_2.id}}}",
                    "alias_domain_id": cls.mail_alias_domain.id,
                }
            )
        )

    @staticmethod
    def make_webhook_headers(payload, secret, msg_id="msg_123", timestamp=None):
        timestamp = timestamp or datetime.now(timezone.utc)
        signature = Webhook(secret).sign(msg_id, timestamp, payload)
        return {
            "Content-Type": "application/json",
            "svix-id": msg_id,
            "svix-timestamp": str(int(timestamp.timestamp())),
            "svix-signature": signature,
        }

    @staticmethod
    def make_raw_email(
        *,
        to_address,
        subject,
        message_id,
        from_address="Sender <sender@example.com>",
        body="Body",
        in_reply_to=None,
        references=None,
        attachments=None,
    ):
        message = EmailMessage()
        message["From"] = from_address
        message["To"] = to_address
        message["Delivered-To"] = to_address
        message["Subject"] = subject
        message["Message-Id"] = message_id
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        if references:
            message["References"] = references
        message.set_content(body)
        for filename, content, mimetype in attachments or []:
            maintype, subtype = mimetype.split("/", 1)
            message.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )
        return message.as_bytes()

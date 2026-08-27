# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import uuid
from email.message import EmailMessage
from urllib.parse import urlsplit

import requests
from requests import HTTPError
from svix.webhooks import Webhook

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .mail_tracking_email import RESEND_TRACKING_EVENT_TYPES

_logger = logging.getLogger(__name__)

# Resend webhook events the account subscribes to.
RESEND_WEBHOOK_EVENT_TYPES = ["email.received", *RESEND_TRACKING_EVENT_TYPES]


class MailResendAccount(models.Model):
    _name = "mail.resend.account"
    _description = "Resend Account"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    api_key = fields.Char(required=True, groups="base.group_system")
    webhook_token = fields.Char(
        required=True,
        copy=False,
        default=lambda self: str(uuid.uuid4()),
    )
    webhook_id = fields.Char(copy=False, readonly=True)
    webhook_signing_secret = fields.Char(copy=False, groups="base.group_system")
    webhook_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("synced", "Synced"),
            ("error", "Error"),
        ],
        default="draft",
        copy=False,
        readonly=True,
    )
    last_sync_error = fields.Text(copy=False, readonly=True)
    company_ids = fields.One2many(
        "res.company", "resend_account_id", string="Companies"
    )
    company_count = fields.Integer(compute="_compute_company_count")
    webhook_url = fields.Char(compute="_compute_webhook_url")
    inbound_fallback_channel_id = fields.Many2one(
        "discuss.channel",
        string="Unmatched Inbound Channel",
        help="Optional channel where inbound emails that do not match any "
        "record or alias are posted, instead of being discarded.",
    )

    _sql_constraints = [
        (
            "mail_resend_account_webhook_token_uniq",
            "unique(webhook_token)",
            "Webhook token must be unique.",
        ),
    ]

    def _compute_webhook_url(self):
        for account in self:
            base_url = (
                account.env["ir.config_parameter"].sudo().get_param("web.base.url")
            )
            account.webhook_url = (
                f"{base_url}/mail_resend_provider/webhook/{account.webhook_token}"
                if base_url
                else False
            )

    def _compute_company_count(self):
        for account in self:
            account.company_count = len(account.company_ids)

    def write(self, values):
        result = super().write(values)
        if "api_key" in values:
            self.company_ids._ensure_resend_outgoing_server()
        return result

    @api.model
    def _get_by_webhook_token(self, token):
        return self.search(
            [("webhook_token", "=", token), ("active", "=", True)], limit=1
        )

    def action_sync_webhook(self):
        notification = False
        for account in self:
            try:
                account._sync_webhook()
            except Exception as err:  # pylint: disable=broad-except
                account.write(
                    {
                        "webhook_status": "error",
                        "last_sync_error": str(err),
                    }
                )
                notification = {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": _("Resend webhook sync failed"),
                        "message": str(err),
                        "type": "danger",
                        "sticky": True,
                    },
                }
            account.company_ids._ensure_resend_outgoing_server()
        return notification or True

    def action_open_companies(self):
        self.ensure_one()
        return {
            "name": _("Companies"),
            "type": "ir.actions.act_window",
            "res_model": "res.company",
            "view_mode": "list,form",
            "domain": [("id", "in", self.company_ids.ids)],
        }

    def _sync_webhook(self):
        self.ensure_one()
        endpoint = self.webhook_url
        self._validate_webhook_url(endpoint)
        webhook_data = self._find_or_sync_webhook(endpoint)
        values = {
            "webhook_id": webhook_data["id"],
            "webhook_status": "synced",
            "last_sync_error": False,
        }
        signing_secret = webhook_data.get("signing_secret")
        if signing_secret:
            values["webhook_signing_secret"] = signing_secret
        self.write(values)

    def _find_or_sync_webhook(self, endpoint):
        self.ensure_one()
        event_types = RESEND_WEBHOOK_EVENT_TYPES
        if self.webhook_id:
            payload = {
                "endpoint": endpoint,
                "events": event_types,
                "status": "enabled",
            }
            self._resend_request(
                "PATCH", f"/webhooks/{self.webhook_id}", payload=payload
            )
            return {"id": self.webhook_id}

        webhooks = self._resend_request("GET", "/webhooks").get("data", [])
        matched = next(
            (item for item in webhooks if item["endpoint"] == endpoint), None
        )
        if matched and self.webhook_signing_secret:
            payload = {
                "endpoint": endpoint,
                "events": event_types,
                "status": "enabled",
            }
            self._resend_request("PATCH", f"/webhooks/{matched['id']}", payload=payload)
            return {"id": matched["id"]}

        if matched and not self.webhook_signing_secret:
            self.write({"webhook_token": str(uuid.uuid4())})
            endpoint = self.webhook_url

        return self._resend_request(
            "POST",
            "/webhooks",
            payload={"endpoint": endpoint, "events": event_types},
        )

    def _verify_webhook_payload(self, raw_payload, headers):
        self.ensure_one()
        webhook = Webhook(self.webhook_signing_secret)
        return webhook.verify(raw_payload.decode("utf-8"), headers)

    def _validate_webhook_url(self, endpoint):
        self.ensure_one()
        if not endpoint:
            raise UserError(
                _(
                    "Missing webhook URL. Configure `web.base.url` before syncing "
                    "the Resend webhook."
                )
            )

        parsed = urlsplit(endpoint)
        if not parsed.scheme or not parsed.netloc:
            raise UserError(_("Invalid webhook URL for Resend: %s", endpoint))
        if parsed.scheme != "https":
            raise UserError(
                _(
                    "Resend requires a publicly accessible HTTPS webhook URL. "
                    "Update `web.base.url` to HTTPS before syncing. Current URL: %s",
                    endpoint,
                )
            )

    def _process_webhook_payload(self, payload, raw_payload, webhook_id=False):
        self.ensure_one()
        event_type = payload.get("type")
        if event_type == "email.received":
            return self._process_inbound_payload(payload, raw_payload)
        if event_type in RESEND_TRACKING_EVENT_TYPES:
            return (
                self.env["mail.tracking.email"]
                .sudo()
                ._resend_event_process(payload, webhook_id)
            )
        _logger.debug("Resend: ignoring webhook event %s", event_type)
        return False

    def _process_inbound_payload(self, payload, raw_payload):
        self.ensure_one()
        email_id = payload["data"]["email_id"]
        inbound = (
            self.env["mail.resend.inbound"]
            .sudo()
            .search(
                [
                    ("account_id", "=", self.id),
                    ("resend_email_id", "=", email_id),
                ],
                limit=1,
            )
        )
        if inbound and inbound.state == "done":
            return

        inbound = inbound or self.env["mail.resend.inbound"].sudo().create(
            {
                "account_id": self.id,
                "resend_email_id": email_id,
                "event_type": payload.get("type"),
                "payload": raw_payload.decode("utf-8"),
                "message_id": payload["data"].get("message_id"),
                "state": "processing",
            }
        )
        inbound._process()

    def _resend_request(self, method, path, payload=None, params=None, timeout=15):
        self.ensure_one()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        response = requests.request(
            method,
            f"https://api.resend.com{path}",
            headers=headers,
            json=payload,
            params=params,
            timeout=timeout,
        )
        try:
            response.raise_for_status()
        except HTTPError as err:
            detail = response.text
            response_payload = None
            try:
                response_payload = response.json()
            except ValueError:
                _logger.debug(
                    "Resend API error response is not JSON for %s %s",
                    method,
                    path,
                )
            if response_payload:
                detail = response_payload.get("message") or detail
            raise UserError(_("Resend API error: %s", detail)) from err
        if not response.content:
            return {}
        return response.json()

    def _retrieve_received_email(self, resend_email_id):
        self.ensure_one()
        return self._resend_request("GET", f"/emails/receiving/{resend_email_id}")

    def _list_received_attachments(self, resend_email_id):
        self.ensure_one()
        return self._resend_request(
            "GET", f"/emails/receiving/{resend_email_id}/attachments"
        ).get("data", [])

    def _download_content(self, download_url, timeout=30):
        response = requests.get(download_url, timeout=timeout)
        response.raise_for_status()
        return response.content

    def _download_raw_email(self, email_data):
        raw_info = email_data.get("raw") or {}
        if raw_info.get("download_url"):
            return self._download_content(raw_info["download_url"])
        return False

    def _build_received_email_bytes(self, email_data):
        message = EmailMessage()
        message["From"] = email_data["from"]
        message["To"] = ", ".join(email_data.get("to", []))
        if email_data.get("cc"):
            message["Cc"] = ", ".join(email_data["cc"])
        if email_data.get("bcc"):
            message["Bcc"] = ", ".join(email_data["bcc"])
        if email_data.get("reply_to"):
            message["Reply-To"] = ", ".join(email_data["reply_to"])
        message["Subject"] = email_data.get("subject") or ""
        message["Message-Id"] = email_data["message_id"]
        for key, value in (email_data.get("headers") or {}).items():
            lower_key = key.lower()
            if lower_key in {
                "from",
                "to",
                "cc",
                "bcc",
                "reply-to",
                "subject",
                "message-id",
            }:
                continue
            message[key] = value
        html_body = email_data.get("html")
        text_body = email_data.get("text")
        if html_body and text_body:
            message.set_content(text_body)
            message.add_alternative(html_body, subtype="html")
        elif html_body:
            message.set_content(html_body, subtype="html")
        else:
            message.set_content(text_body or "")

        for attachment in self._list_received_attachments(email_data["id"]):
            content = self._download_content(attachment["download_url"])
            mimetype = attachment.get("content_type") or "application/octet-stream"
            maintype, subtype = mimetype.split("/", 1)
            disposition = attachment.get("content_disposition")
            message.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=attachment.get("filename"),
                disposition=disposition or None,
                cid=attachment.get("content_id") or None,
            )
        return message.as_bytes()

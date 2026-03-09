# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import re
from email.parser import BytesParser
from email.policy import SMTP

from odoo import fields, models, tools

_logger = logging.getLogger(__name__)
MESSAGE_ID_PATTERN = re.compile(r"<[^<>\s]+>")


class MailResendInbound(models.Model):
    _name = "mail.resend.inbound"
    _description = "Resend Inbound Email"
    _order = "id desc"

    account_id = fields.Many2one(
        "mail.resend.account", required=True, ondelete="cascade", index=True
    )
    resend_email_id = fields.Char(required=True, index=True, copy=False)
    event_type = fields.Char()
    message_id = fields.Char(index=True)
    payload = fields.Text()
    state = fields.Selection(
        [
            ("processing", "Processing"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        default="processing",
        readonly=True,
        copy=False,
    )
    error_message = fields.Text(readonly=True, copy=False)
    mail_message_id = fields.Many2one("mail.message", readonly=True, copy=False)
    route_id = fields.Many2one("mail.resend.route", readonly=True, copy=False)
    correlation_method = fields.Selection(
        [
            ("reply_token", "Reply Token"),
            ("provider_message_id", "Provider Message-Id"),
            ("native", "Native"),
            ("none", "None"),
        ],
        readonly=True,
        copy=False,
    )
    correlated_message_id = fields.Many2one("mail.message", readonly=True, copy=False)
    target_model = fields.Char(readonly=True, copy=False)
    target_res_id = fields.Integer(readonly=True, copy=False)

    _sql_constraints = [
        (
            "mail_resend_inbound_account_email_uniq",
            "unique(account_id, resend_email_id)",
            "Received email must be unique per Resend account.",
        )
    ]

    def _process(self):
        for inbound in self:
            try:
                email_data = inbound.account_id._retrieve_received_email(
                    inbound.resend_email_id
                )
                raw_bytes = inbound.account_id._download_raw_email(email_data)
                raw_bytes = raw_bytes or inbound.account_id._build_received_email_bytes(
                    email_data
                )
                (
                    route,
                    correlation_method,
                    correlated_message,
                    raw_bytes,
                    target_model,
                    target_res_id,
                ) = inbound._prepare_correlated_message(raw_bytes)
                allowed_company_ids = inbound.account_id.company_ids.ids
                inbound.env["mail.thread"].with_context(
                    allowed_company_ids=allowed_company_ids or inbound.env.companies.ids
                ).sudo().message_process(
                    target_model,
                    raw_bytes,
                    thread_id=target_res_id,
                    save_original=True,
                )
                mail_message = (
                    inbound.env["mail.message"]
                    .sudo()
                    .search(
                        [("message_id", "=", email_data["message_id"])],
                        limit=1,
                        order="id desc",
                    )
                )
                if not correlation_method and mail_message:
                    correlation_method = "native"
                if not correlated_message and mail_message.parent_id:
                    correlated_message = mail_message.parent_id
                if route:
                    route.write({"state": "matched"})
                inbound.write(
                    {
                        "state": "done",
                        "correlated_message_id": correlated_message.id,
                        "correlation_method": correlation_method or "none",
                        "error_message": False,
                        "mail_message_id": mail_message.id,
                        "route_id": route.id,
                        "target_model": mail_message.model or target_model,
                        "target_res_id": mail_message.res_id or target_res_id or 0,
                        "message_id": email_data["message_id"],
                    }
                )
            except Exception as err:  # pylint: disable=broad-except
                _logger.exception(
                    "Failed to process Resend inbound email %s", inbound.resend_email_id
                )
                inbound.write(
                    {
                        "state": "error",
                        "error_message": str(err),
                    }
                )
                raise

    def _prepare_correlated_message(self, raw_bytes):
        self.ensure_one()
        message = BytesParser(policy=SMTP).parsebytes(raw_bytes)
        route, correlation_method = self._find_route(message)
        correlated_message = (
            route.mail_message_id if route else self.env["mail.message"]
        )
        target_model = route.model if route else None
        target_res_id = route.res_id if route else None
        if route:
            message = self._apply_route_to_message(message, route)
            raw_bytes = message.as_bytes()
        return (
            route,
            correlation_method,
            correlated_message,
            raw_bytes,
            target_model,
            target_res_id,
        )

    def _find_route(self, message):
        self.ensure_one()
        route_model = self.env["mail.resend.route"].sudo()
        reply_addresses = self._extract_recipients(message)
        if reply_addresses:
            route = route_model.search(
                [
                    ("account_id", "=", self.account_id.id),
                    ("reply_address", "in", reply_addresses),
                ],
                limit=1,
                order="id desc",
            )
            if route:
                return route, "reply_token"

        provider_message_ids = self._extract_message_ids(message)
        if provider_message_ids:
            route = route_model.search(
                [
                    ("account_id", "=", self.account_id.id),
                    ("provider_message_id", "in", provider_message_ids),
                ],
                limit=1,
                order="id desc",
            )
            if route:
                return route, "provider_message_id"
        return route_model, False

    def _extract_recipients(self, message):
        normalized_addresses = []
        for header_name in ("Delivered-To", "To", "Cc", "Bcc"):
            header_value = message.get(header_name)
            if not header_value:
                continue
            normalized_addresses.extend(
                filter(
                    None,
                    (
                        tools.email_normalize(address, strict=False)
                        for address in tools.email_split(header_value)
                    ),
                )
            )
        return list(dict.fromkeys(normalized_addresses))

    def _extract_message_ids(self, message):
        header_values = []
        for header_name in ("In-Reply-To", "References"):
            header_value = message.get(header_name)
            if header_value:
                header_values.append(header_value)
        message_ids = []
        for header_value in header_values:
            message_ids.extend(MESSAGE_ID_PATTERN.findall(header_value))
        return list(dict.fromkeys(message_ids))

    def _apply_route_to_message(self, message, route):
        canonical_message_id = route.mail_message_id.message_id
        if route.reply_address and self._is_direct_catchall_recipient(message, route):
            self._set_header(message, "To", route.reply_address)
            self._set_header(message, "Delivered-To", route.reply_address)
        if canonical_message_id:
            self._set_header(message, "In-Reply-To", canonical_message_id)
            references = self._merge_references(
                message.get("References"),
                canonical_message_id,
            )
            self._set_header(message, "References", references)
        return message

    def _is_direct_catchall_recipient(self, message, route):
        recipients = self._extract_recipients(message)
        catchall_email = (
            tools.email_normalize(route.company_id.catchall_email, strict=False)
            or route.company_id.catchall_email
        )
        return bool(
            recipients and catchall_email and set(recipients) == {catchall_email}
        )

    def _merge_references(self, current_references, canonical_message_id):
        references = MESSAGE_ID_PATTERN.findall(current_references or "")
        if canonical_message_id not in references:
            references.append(canonical_message_id)
        return " ".join(references)

    def _set_header(self, message, header_name, value):
        while header_name in message:
            del message[header_name]
        if value:
            message[header_name] = value

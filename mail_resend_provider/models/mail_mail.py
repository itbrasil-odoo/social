# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
import uuid

from odoo import _, api, models, tools
from odoo.exceptions import UserError

MESSAGE_ID_PATTERN = re.compile(r"<[^<>\s]+>")


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
                    threadable=self._resend_is_threadable_values(values),
                )
            )
        records = super().create(values_list)
        records.filtered(
            lambda mail: mail.state == "outgoing"
        )._resend_prepare_outgoing_mails()
        return records

    def _split_by_mail_configuration(self):
        self.filtered(
            lambda mail: not mail.mail_server_id and mail._resend_get_company()
        )._assign_resend_outgoing_server()
        return super()._split_by_mail_configuration()

    def send(self, auto_commit=False, raise_exception=False, post_send_callback=None):
        outgoing_mails = self.filtered(lambda mail: mail.state == "outgoing")
        outgoing_mails._resend_prepare_outgoing_mails()
        return super().send(
            auto_commit=auto_commit,
            raise_exception=raise_exception,
            post_send_callback=post_send_callback,
        )

    def _postprocess_sent_message(
        self, success_pids, failure_reason=False, failure_type=None
    ):
        self.exists().filtered(
            lambda mail: mail.state == "sent" and mail.mail_server_id.resend_managed
        )._resend_mark_routes_sent()
        return super()._postprocess_sent_message(
            success_pids=success_pids,
            failure_reason=failure_reason,
            failure_type=failure_type,
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

    @api.model
    def _resend_is_threadable_values(self, values):
        mail_message = self.env["mail.message"]
        if values.get("mail_message_id"):
            mail_message = mail_message.browse(values["mail_message_id"])
            return bool(
                mail_message.exists()
                and mail_message.is_thread_message()
                and not mail_message.reply_to_force_new
            )
        return bool(
            values.get("model")
            and values.get("res_id")
            and values.get("message_type", "email") != "user_notification"
            and not values.get("reply_to_force_new")
        )

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
        self,
        email_from=False,
        reply_to=False,
        company=False,
        mail_server=False,
        threadable=False,
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
        if not reply_to and email_from and not threadable:
            values["reply_to"] = email_from
        return values

    @api.model
    def _resend_parse_contact(self, value):
        if not value:
            return "", False
        display_name, email = tools.parse_contact_from_email(value)
        normalized_email = tools.email_normalize(email or value, strict=False)
        return display_name or "", normalized_email or email or value

    @api.model
    def _resend_reply_to_matches(self, reply_to, expected_reply_to):
        current_name, current_email = self._resend_parse_contact(reply_to)
        expected_name, expected_email = self._resend_parse_contact(expected_reply_to)
        return bool(
            current_email
            and expected_email
            and current_email == expected_email
            and current_name == expected_name
        )

    @api.model
    def _resend_format_reply_to(self, reply_to, target_email):
        normalized_target = (
            tools.email_normalize(target_email, strict=False) or target_email
        )
        display_name, current_email = tools.parse_contact_from_email(reply_to or "")
        normalized_current = tools.email_normalize(
            current_email or reply_to, strict=False
        )
        if normalized_current == normalized_target:
            return reply_to or normalized_target
        if display_name:
            return tools.formataddr((display_name, normalized_target))
        return normalized_target

    @api.model
    def _resend_build_reply_address(self, token, company):
        return (
            tools.email_normalize(
                f"reply-{token}@{company.alias_domain_id.name}",
                strict=False,
            )
            or f"reply-{token}@{company.alias_domain_id.name}"
        )

    def _resend_is_threadable_mail(self):
        self.ensure_one()
        return bool(
            self.model
            and self.res_id
            and self.message_type != "user_notification"
            and not self.reply_to_force_new
        )

    def _resend_get_expected_reply_to(self):
        self.ensure_one()
        if not self._resend_is_threadable_mail():
            return False
        return self.env["mail.message"]._get_reply_to(
            {
                "email_from": self.email_from,
                "message_type": self.message_type,
                "model": self.model,
                "res_id": self.res_id,
            }
        )

    @api.model
    def _resend_merge_references(self, references, canonical_message_id):
        reference_ids = list(
            dict.fromkeys(MESSAGE_ID_PATTERN.findall(references or ""))
        )
        if canonical_message_id and canonical_message_id not in reference_ids:
            reference_ids.append(canonical_message_id)
        return " ".join(reference_ids)

    def _resend_get_route(self):
        self.ensure_one()
        route_model = self.env["mail.resend.route"].sudo()
        route = route_model.search(
            [("mail_message_id", "=", self.mail_message_id.id)], limit=1
        )
        if route:
            return route
        if self.id:
            return route_model.search([("mail_mail_id", "=", self.id)], limit=1)
        return route_model

    def _resend_prepare_route_data(self):
        self.ensure_one()
        mail_server = self.mail_server_id.sudo()
        if not mail_server or not mail_server.resend_managed:
            return False
        if not self._resend_is_threadable_mail():
            return False

        company = self._resend_get_sender_company(
            company=self._resend_get_company(),
            mail_server=mail_server,
        )
        account = mail_server.resend_account_id or company.resend_account_id
        route = self._resend_get_route()
        expected_reply_to = self._resend_get_expected_reply_to()
        current_reply_to = self.reply_to or expected_reply_to
        route_reply_to = (
            self._resend_format_reply_to(current_reply_to, route.reply_address)
            if route.reply_address and current_reply_to
            else False
        )
        is_auto_reply_to = bool(
            expected_reply_to
            and (
                not self.reply_to
                or self._resend_reply_to_matches(self.reply_to, expected_reply_to)
                or (route_reply_to and self.reply_to == route_reply_to)
            )
        )

        values = {
            "account_id": account.id,
            "company_id": company.id,
            "mail_mail_id": self.id,
            "mail_message_id": self.mail_message_id.id,
            "model": self.model,
            "provider_message_id": False,
            "reply_address": False,
            "reply_token": False,
            "res_id": self.res_id,
            "state": "draft",
        }
        mail_values = {}
        canonical_message_id = self.mail_message_id.message_id
        merged_references = self._resend_merge_references(
            self.references,
            canonical_message_id,
        )
        if merged_references and merged_references != (self.references or ""):
            mail_values["references"] = merged_references
        if is_auto_reply_to and current_reply_to:
            token = route.reply_token or uuid.uuid4().hex
            reply_address = self._resend_build_reply_address(token, company)
            values.update(
                {
                    "reply_address": reply_address,
                    "reply_token": token,
                }
            )
            technical_reply_to = self._resend_format_reply_to(
                current_reply_to,
                reply_address,
            )
            if technical_reply_to != self.reply_to:
                mail_values["reply_to"] = technical_reply_to
        return route, values, mail_values

    def _resend_ensure_route(self):
        route_model = self.env["mail.resend.route"].sudo()
        for mail in self:
            route_data = mail._resend_prepare_route_data()
            if not route_data:
                continue
            route, values, mail_values = route_data
            route = route or route_model.create(values)
            if route:
                route.write(values)
            if mail_values:
                mail.write(mail_values)

    def _resend_mark_routes_sent(self):
        for mail in self.exists():
            route = mail._resend_get_route()
            if route:
                route.write(
                    {
                        "mail_mail_id": mail.id,
                        "provider_message_id": mail.message_id,
                        "state": "sent",
                    }
                )

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
                threadable=mail._resend_is_threadable_mail(),
            )
            if values:
                mail.write(values)
            mail._resend_ensure_route()

    def _assign_resend_outgoing_server(self):
        for mail in self.filtered(lambda record: not record.mail_server_id):
            company = mail._resend_get_company()
            if company and company.resend_account_id:
                mail.mail_server_id = company._ensure_resend_outgoing_server()

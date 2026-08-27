# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging

from werkzeug.exceptions import BadRequest, NotFound

from odoo.http import Controller, request, route

_logger = logging.getLogger(__name__)


class MailResendProviderController(Controller):
    @route(
        "/mail_resend_provider/webhook/<string:token>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def resend_webhook(self, token, **kwargs):
        account = request.env["mail.resend.account"].sudo()._get_by_webhook_token(token)
        if not account:
            raise NotFound()

        raw_payload = request.httprequest.get_data()
        headers = {
            "svix-id": request.httprequest.headers.get("svix-id"),
            "svix-timestamp": request.httprequest.headers.get("svix-timestamp"),
            "svix-signature": request.httprequest.headers.get("svix-signature"),
        }
        try:
            payload = account._verify_webhook_payload(raw_payload, headers)
        except Exception as err:  # pylint: disable=broad-except
            _logger.warning(
                "Invalid Resend webhook for account %s: %s", account.display_name, err
            )
            raise BadRequest() from err

        account._process_webhook_payload(
            payload, raw_payload, webhook_id=headers.get("svix-id")
        )
        return request.make_response(
            json.dumps({}),
            [("Content-Type", "application/json")],
        )

# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
from datetime import datetime, timezone

from odoo import api, fields, models, tools

_logger = logging.getLogger(__name__)

# Resend webhook event types that map to a mail tracking state.
RESEND_TRACKING_EVENT_TYPES = (
    "email.sent",
    "email.delivered",
    "email.delivery_delayed",
    "email.bounced",
    "email.complained",
    "email.failed",
    "email.suppressed",
)


class MailTrackingEmail(models.Model):
    _inherit = "mail.tracking.email"

    @api.model
    def _resend_event2type(self, payload, default="UNKNOWN"):
        """Map a Resend webhook event to the ``mail.tracking.event`` event type."""
        event_type = payload.get("type")
        data = payload.get("data") or {}
        if event_type == "email.bounced":
            bounce_type = (data.get("bounce") or {}).get("type")
            # Resend bounce types: Permanent (hard), Transient (soft), Undetermined.
            return "hard_bounce" if bounce_type == "Permanent" else "soft_bounce"
        equivalents = {
            "email.sent": "sent",
            "email.delivered": "delivered",
            "email.delivery_delayed": "deferral",
            "email.complained": "spam",
            "email.failed": "reject",
            "email.suppressed": "reject",
        }
        return equivalents.get(event_type, default)

    @api.model
    def _resend_event_recipient(self, data):
        recipients = data.get("to") or []
        if isinstance(recipients, str):
            recipients = [recipients]
        return recipients[0] if recipients else False

    @api.model
    def _resend_parse_timestamp(self, value):
        if not value:
            return False
        try:
            normalized = value.replace("Z", "+00:00") if "Z" in value else value
            return datetime.fromisoformat(normalized).timestamp()
        except (ValueError, AttributeError):
            return False

    @api.model
    def _resend_find_tracking(self, data):
        """Correlate a Resend event with its ``mail.tracking.email`` record.

        Resend bounce/complaint webhooks do not carry the original Message-Id, so
        the tracking record is matched by recipient (each event targets a single
        recipient) refined by subject, picking the most recent one.
        """
        recipient = self._resend_event_recipient(data)
        if not recipient:
            return self.browse()
        recipient_address = (
            tools.email_normalize(recipient, strict=False) or recipient.lower()
        )
        base_domain = [("recipient_address", "=", recipient_address)]
        subject = data.get("subject")
        if subject:
            tracking = self.sudo().search(
                base_domain + [("name", "=", subject)], order="time desc", limit=1
            )
            if tracking:
                return tracking
        return self.sudo().search(base_domain, order="time desc", limit=1)

    def _resend_metadata(self, payload, webhook_id, metadata):
        data = payload.get("data") or {}
        event_type = payload.get("type")
        ts = self._resend_parse_timestamp(
            payload.get("created_at") or data.get("created_at")
        )
        if ts:
            dt = datetime.fromtimestamp(ts, timezone.utc)
            metadata.update(
                {
                    "timestamp": ts,
                    "time": fields.Datetime.to_string(dt),
                    "date": fields.Date.to_string(dt),
                }
            )
        recipient = self._resend_event_recipient(data)
        if recipient:
            metadata["recipient"] = recipient
        metadata["resend_event_id"] = webhook_id
        if event_type == "email.bounced":
            bounce = data.get("bounce") or {}
            metadata.update(
                {
                    "bounce_type": bounce.get("type"),
                    "bounce_description": bounce.get("message"),
                    "error_type": bounce.get("subType"),
                    "error_description": bounce.get("message"),
                }
            )
        elif event_type == "email.complained":
            metadata.update(
                {
                    "error_type": "spam",
                    "error_description": (
                        f"Recipient '{recipient}' marked this email as spam"
                    ),
                }
            )
        elif event_type == "email.failed":
            failed = data.get("failed") or {}
            metadata.update(
                {
                    "error_type": "failed",
                    "error_description": failed.get("reason"),
                }
            )
        elif event_type == "email.suppressed":
            metadata.update(
                {
                    "error_type": "suppressed",
                    "error_description": (
                        f"Recipient '{recipient}' is on the Resend suppression list"
                    ),
                }
            )
        return metadata

    @api.model
    def _resend_event_process(self, payload, webhook_id, metadata=None):
        """Process a Resend status webhook into a ``mail.tracking.event``."""
        metadata = metadata or {}
        event_type = payload.get("type")
        data = payload.get("data") or {}
        # Skip already processed webhooks (idempotency via Svix message id).
        if webhook_id:
            existing = (
                self.env["mail.tracking.event"]
                .sudo()
                .search([("resend_event_id", "=", webhook_id)], limit=1)
            )
            if existing:
                _logger.debug("Resend event already processed: %s", webhook_id)
                return existing
        state = self._resend_event2type(payload)
        if state == "UNKNOWN":
            _logger.debug("Resend: ignoring unmapped event %s", event_type)
            return self.env["mail.tracking.event"]
        tracking = self._resend_find_tracking(data)
        if not tracking:
            _logger.info(
                "Resend: no tracking email for event %s (email_id=%s, to=%s)",
                event_type,
                data.get("email_id"),
                self._resend_event_recipient(data),
            )
            return self.env["mail.tracking.event"]
        metadata = tracking._resend_metadata(payload, webhook_id, metadata)
        _logger.info(
            "Resend: importing event %s (%s) for %s",
            webhook_id,
            event_type,
            metadata.get("recipient"),
        )
        return tracking.event_create(state, metadata)

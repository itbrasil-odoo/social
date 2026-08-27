# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.tests.common import tagged

from .common import MailResendProviderCommon


@tagged("-at_install", "post_install")
class TestMailResendTracking(MailResendProviderCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tracking_partner = cls.env["res.partner"].create(
            {
                "name": "Bounce Target",
                "email": "bounce.target@example.com",
            }
        )
        cls.tracking = cls.env["mail.tracking.email"].create(
            {
                "name": "Tracked Subject",
                "recipient": "bounce.target@example.com",
                "sender": "notifications@example.com",
                "partner_id": cls.tracking_partner.id,
            }
        )

    def _payload(self, event_type, **data):
        base = {
            "email_id": "email_tracking_1",
            "to": ["bounce.target@example.com"],
            "subject": "Tracked Subject",
            "created_at": "2026-06-19T19:12:18.000Z",
        }
        base.update(data)
        return {
            "type": event_type,
            "created_at": "2026-06-19T19:12:18.126Z",
            "data": base,
        }

    def _process(self, payload, webhook_id="msg_track_1"):
        return (
            self.env["mail.tracking.email"]
            .sudo()
            ._resend_event_process(payload, webhook_id)
        )

    def test_event2type_mapping(self):
        Tracking = self.env["mail.tracking.email"]
        self.assertEqual(
            Tracking._resend_event2type(self._payload("email.delivered")), "delivered"
        )
        self.assertEqual(
            Tracking._resend_event2type(self._payload("email.complained")), "spam"
        )
        self.assertEqual(
            Tracking._resend_event2type(self._payload("email.failed")), "reject"
        )
        self.assertEqual(
            Tracking._resend_event2type(self._payload("email.suppressed")), "reject"
        )
        self.assertEqual(
            Tracking._resend_event2type(
                self._payload("email.bounced", bounce={"type": "Permanent"})
            ),
            "hard_bounce",
        )
        self.assertEqual(
            Tracking._resend_event2type(
                self._payload("email.bounced", bounce={"type": "Transient"})
            ),
            "soft_bounce",
        )
        self.assertEqual(
            Tracking._resend_event2type(self._payload("email.opened")), "UNKNOWN"
        )

    def test_delivered_updates_state(self):
        event = self._process(self._payload("email.delivered"))
        self.assertEqual(event.event_type, "delivered")
        self.assertEqual(event.tracking_email_id, self.tracking)
        self.assertEqual(self.tracking.state, "delivered")

    def test_hard_bounce_sets_partner_bounced(self):
        payload = self._payload(
            "email.bounced",
            bounce={
                "type": "Permanent",
                "subType": "General",
                "message": "Mailbox does not exist",
            },
        )
        event = self._process(payload)
        self.assertEqual(event.event_type, "hard_bounce")
        self.assertEqual(self.tracking.state, "bounced")
        self.assertEqual(self.tracking.bounce_type, "Permanent")
        self.assertEqual(self.tracking.bounce_description, "Mailbox does not exist")
        self.assertTrue(self.tracking_partner.email_bounced)

    def test_complaint_sets_partner_bounced(self):
        event = self._process(self._payload("email.complained"))
        self.assertEqual(event.event_type, "spam")
        self.assertEqual(self.tracking.state, "spam")
        self.assertTrue(self.tracking_partner.email_bounced)

    def test_suppressed_marks_rejected(self):
        event = self._process(self._payload("email.suppressed"))
        self.assertEqual(event.event_type, "reject")
        self.assertEqual(self.tracking.state, "rejected")
        self.assertTrue(self.tracking_partner.email_bounced)

    def test_idempotent_by_webhook_id(self):
        payload = self._payload("email.delivered")
        first = self._process(payload, webhook_id="msg_dup")
        second = self._process(payload, webhook_id="msg_dup")
        self.assertEqual(first, second)
        self.assertEqual(
            self.env["mail.tracking.event"].search_count(
                [("resend_event_id", "=", "msg_dup")]
            ),
            1,
        )

    def test_unmatched_recipient_is_ignored(self):
        payload = self._payload("email.delivered", to=["unknown@example.com"])
        event = self._process(payload, webhook_id="msg_unmatched")
        self.assertFalse(event)

    def test_dispatch_from_account(self):
        payload = self._payload("email.delivered")
        self.resend_account._process_webhook_payload(
            payload, b"{}", webhook_id="msg_dispatch"
        )
        self.assertEqual(self.tracking.state, "delivered")

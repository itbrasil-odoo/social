Stores the Resend delivery status events (sent, delivered, delayed, bounced,
complained, failed, suppressed) as `mail.tracking.event`, on top of the OCA
`mail_tracking` module.

It is split from `mail_resend_provider` so that sending and receiving through
Resend do not depend on `mail_tracking`, which is not available on every Odoo
version. Without this module the Resend webhook only subscribes to received
emails.

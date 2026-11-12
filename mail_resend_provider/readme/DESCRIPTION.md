Manage outgoing email and inbound webhook processing through Resend.

Delivery status events (sent, delivered, bounced...) are stored by the
`mail_resend_provider_tracking` module, on top of the OCA `mail_tracking`
infrastructure. Without it, the Resend webhook only subscribes to received
emails.

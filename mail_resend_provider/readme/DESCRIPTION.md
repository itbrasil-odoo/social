Manage outgoing email and inbound webhook processing through Resend.

Delivery status events (sent, delivered, bounced...) are stored by the
`mail_resend_provider_tracking` module, on top of the OCA `mail_tracking`
infrastructure. Without it, the Resend webhook only subscribes to received
emails.

Resend replaces the Message-ID of the emails it delivers. Replies sent to the
reply address are matched by its token; replies sent to the From address only
quote the new Message-ID. For threaded emails, the module keeps the Resend id of
each submission and fetches that Message-ID from the API (cron every 5 minutes,
or right away when a reply from that recipient arrives first), so those replies
also reach their thread.

- Use the company settings to bind a Resend account.
- Odoo provisions the managed SMTP server automatically.
- Incoming Resend webhooks are routed through the native Odoo mail aliases.
- Delivery status webhooks (`email.sent`, `email.delivered`,
  `email.delivery_delayed`, `email.bounced`, `email.complained`,
  `email.failed`, `email.suppressed`) are recorded as ``mail.tracking.email``
  events. Hard bounces, complaints and suppressions flag the partner email as
  bounced and feed the standard auto-blacklist.
- Optionally set an *Unmatched Inbound Channel* on the Resend account so inbound
  emails that match no record or alias are posted there instead of being
  rejected.

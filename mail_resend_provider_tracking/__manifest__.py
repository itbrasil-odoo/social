# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Mail Resend Provider - Tracking",
    "summary": "Store Resend delivery status events on mail_tracking",
    "version": "19.0.1.0.0",
    "category": "Discuss",
    "license": "AGPL-3",
    "author": "Odoo Community Association (OCA), IT Brasil, Renan Teixeira",
    "website": "https://github.com/OCA/social",
    "depends": ["mail_resend_provider", "mail_tracking"],
    # mail_tracking is not available on 19.0 yet (OCA/social#1867 was closed
    # unmerged). Make this module installable once it is migrated.
    "installable": False,
    "auto_install": True,
}

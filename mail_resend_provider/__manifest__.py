# Copyright 2026 IT Brasil, Renan Teixeira
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Mail Resend Provider",
    "summary": "Send and receive emails through Resend",
    "version": "18.0.1.0.1",
    "category": "Discuss",
    "license": "AGPL-3",
    "author": "Odoo Community Association (OCA), IT Brasil, Renan Teixeira",
    "website": "https://github.com/OCA/social",
    "depends": ["mail"],
    "external_dependencies": {"python": ["svix"]},
    "data": [
        "security/ir.model.access.csv",
        "views/mail_resend_account_views.xml",
        "views/ir_mail_server_views.xml",
        "views/res_config_settings_views.xml",
        "views/mail_resend_menus.xml",
    ],
    "installable": True,
}

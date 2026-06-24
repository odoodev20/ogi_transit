from odoo import models, fields, api, _
from odoo.exceptions import AccessError
from markupsafe import Markup

class ResPartner(models.Model):
    _inherit = 'res.partner'

    phone_1 = fields.Char(string="Phone 1", copy=False)
    phone_2 = fields.Char(string="Phone 2")

    # Phone Change Request Workflow Fields
    pending_phone = fields.Char(string="Pending Phone Request", tracking=True, help="Propose a new primary phone number here.")
    old_phone = fields.Char(string="Previous Phone", readonly=True, tracking=True, help="Automatically stores the old phone number in read-only mode after a change is approved.")

    # Hidden legacy field to prevent breaking Vendor Bills
    is_freight_forwarder = fields.Boolean(string="Is Freight Forwarder")

    # Contact Type Selection
    contact_type = fields.Selection([
        ('customer', 'Customer'),
        ('freight_forwarder', 'Freight Forwarder'),
        ('china_cargo', 'China Cargo'),
        ('dubai_cargo', 'Dubai Cargo'),
        ('other', 'Other Suppliers')
    ], string="Contact Type", default='customer', required=True, tracking=True)

    deposit_usd = fields.Float(string='Deposit Balance (USD)', default=0.0, readonly=True, tracking=True)
    deposit_gnf = fields.Float(string='Deposit Balance (GNF)', default=0.0, readonly=True, tracking=True)

    # ==========================================
    # NEW: Smart Button Fields & Compute Methods
    # ==========================================
    ogi_invoice_count = fields.Integer(compute='_compute_ogi_stats')
    ogi_bill_count = fields.Integer(compute='_compute_ogi_stats')
    ogi_receipt_count = fields.Integer(compute='_compute_ogi_stats')
    ogi_display_total = fields.Char(compute='_compute_ogi_stats', string="Total Invoiced/Billed")

    def _compute_ogi_stats(self):
        for partner in self:
            # 1. Customer Invoices
            invoices = self.env['ogi.transit.invoice'].search([('partner_id', '=', partner.id), ('state', '!=', 'canceled')])
            partner.ogi_invoice_count = len(invoices)
            
            # 2. Vendor Bills
            bills = self.env['ogi.transit.vendor.bill'].search([('partner_id', '=', partner.id)])
            partner.ogi_bill_count = len(bills)
            
            # 3. Receipts / Transactions
            receipts = self.env['ogi.transit.transaction'].search([('partner_id', '=', partner.id), ('state', '!=', 'cancelled')])
            partner.ogi_receipt_count = len(receipts)

            # 4. Format the Display Total dynamically
            # REFACTORED: Removed f-strings and used standard string formatting for parser safety
            if partner.contact_type == 'customer':
                usd_total = sum(invoices.filtered(lambda i: i.currency == 'USD').mapped('amount_total'))
                gnf_total = sum(invoices.filtered(lambda i: i.currency == 'GNF').mapped('amount_total'))
                
                if usd_total > 0 and gnf_total > 0:
                    partner.ogi_display_total = "$%s | %s FG" % (format(usd_total, ",.0f"), format(gnf_total, ",.0f"))
                elif usd_total > 0:
                    partner.ogi_display_total = "$%s" % format(usd_total, ",.0f")
                else:
                    partner.ogi_display_total = "%s FG" % format(gnf_total, ",.0f")
            else:
                usd_total = sum(bills.filtered(lambda b: b.currency == 'USD').mapped('amount_total'))
                gnf_total = sum(bills.filtered(lambda b: b.currency == 'GNF').mapped('amount_total'))
                
                if usd_total > 0 and gnf_total > 0:
                    partner.ogi_display_total = "$%s | %s FG" % (format(usd_total, ",.0f"), format(gnf_total, ",.0f"))
                elif usd_total > 0:
                    partner.ogi_display_total = "$%s" % format(usd_total, ",.0f")
                else:
                    partner.ogi_display_total = "%s FG" % format(gnf_total, ",.0f")

    def action_view_ogi_invoices(self):
        self.ensure_one()
        return {
            # REFACTORED: Wrapped in _()
            'name': _('Customer Invoices'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.invoice',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    def action_view_ogi_bills(self):
        self.ensure_one()
        return {
            # REFACTORED: Wrapped in _()
            'name': _('Vendor Bills'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.vendor.bill',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }

    def action_view_ogi_receipts(self):
        self.ensure_one()
        return {
            # REFACTORED: Wrapped in _()
            'name': _('Payments & Receipts'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.transaction',
            'view_mode': 'list,form',
            'domain': [('partner_id', '=', self.id)],
            'context': {'default_partner_id': self.id},
        }
    # ==========================================

    _sql_constraints = [
        ('phone_1_unique', 'unique(phone_1)', 'Phone 1 must be unique in the database. This is the primary identifier.')
    ]

    @api.onchange('contact_type')
    def _onchange_contact_type(self):
        if self.contact_type == 'freight_forwarder':
            self.is_freight_forwarder = True
        else:
            self.is_freight_forwarder = False

    def action_approve_phone_change(self):
        for partner in self:
            is_manager = self.env.user.has_group('ogi_transit.group_ogi_gerant')
            is_ceo = self.env.user.has_group('ogi_transit.group_ogi_pdg')
            is_admin = self.env.user.has_group('ogi_transit.group_ogi_admin')

            if not (is_manager or is_ceo or is_admin):
                # REFACTORED: Wrapped in _()
                raise AccessError(_("Access Denied: Only a Manager or CEO can approve a phone number change."))

            if partner.pending_phone:
                partner.old_phone = partner.phone_1
                partner.phone_1 = partner.pending_phone
                partner.pending_phone = False
                # REFACTORED: Converted f-string to %s formatting and wrapped in _()
                partner.message_post(body=Markup(_("<strong>Phone Number Updated:</strong> Changed from %s to %s by Management.")) % (partner.old_phone, partner.phone_1))

    def write(self, vals):
        protected_types = ['china_cargo', 'dubai_cargo']
        is_admin = self.env.user.has_group('ogi_transit.group_ogi_admin')
        is_manager = self.env.user.has_group('ogi_transit.group_ogi_gerant')
        is_ceo = self.env.user.has_group('ogi_transit.group_ogi_pdg')

        if 'phone_1' in vals:
            for partner in self:
                if partner.phone_1 and partner.phone_1 != vals['phone_1']:
                    if not (is_admin or is_manager or is_ceo):
                        # REFACTORED: Wrapped in _()
                        raise AccessError(_(
                            "Access Denied: You do not have permission to directly change a customer's primary phone number. "
                            "Please enter the new number in the 'Pending Phone Request' field and ask a Manager or CEO to approve it."
                        ))

        if not is_admin:
            for partner in self:
                if partner.contact_type in protected_types:
                    # REFACTORED: Wrapped in _()
                    raise AccessError(_("Access Denied: Only Admin users can edit China Cargo or Dubai Cargo contacts."))

            if vals.get('contact_type') in protected_types:
                # REFACTORED: Wrapped in _()
                raise AccessError(_("Access Denied: Only Admin users can classify a contact as China Cargo or Dubai Cargo."))

        return super().write(vals)

    def unlink(self):
        protected_types = ['china_cargo', 'dubai_cargo']
        if not self.env.user.has_group('ogi_transit.group_ogi_admin'):
            for partner in self:
                if partner.contact_type in protected_types:
                    # REFACTORED: Wrapped in _()
                    raise AccessError(_("Access Denied: Only Admin users can delete China Cargo or Dubai Cargo contacts."))
        return super().unlink()
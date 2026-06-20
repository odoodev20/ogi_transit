from odoo import models, fields, api
from odoo.exceptions import AccessError

class ResPartner(models.Model):
    _inherit = 'res.partner'

    phone_1 = fields.Char(string="Phone 1", copy=False)
    phone_2 = fields.Char(string="Phone 2")
    
    # Hidden legacy field to prevent breaking Vendor Bills
    is_freight_forwarder = fields.Boolean(string="Is Freight Forwarder")

    # NEW: Contact Type Selection
    contact_type = fields.Selection([
        ('customer', 'Customer'),
        ('freight_forwarder', 'Freight Forwarder'),
        ('china_cargo', 'China Cargo'),
        ('dubai_cargo', 'Dubai Cargo'),
        ('other', 'Other Suppliers')
    ], string="Contact Type", default='customer', required=True, tracking=True)

    deposit_usd = fields.Float(string='Deposit Balance (USD)', default=0.0, readonly=True, tracking=True)
    deposit_gnf = fields.Float(string='Deposit Balance (GNF)', default=0.0, readonly=True, tracking=True)

    _sql_constraints = [
        ('phone_1_unique', 'unique(phone_1)', 'Phone 1 must be unique in the database! This is the primary identifier.')
    ]

    @api.onchange('contact_type')
    def _onchange_contact_type(self):
        if self.contact_type == 'freight_forwarder':
            self.is_freight_forwarder = True
        else:
            self.is_freight_forwarder = False

    # STRICT SECURITY: Block Edits
    def write(self, vals):
        protected_types = ['china_cargo', 'dubai_cargo']
        
        # If the user is NOT an Admin, enforce the rules
        if not self.env.user.has_group('ogi_transit.group_ogi_admin'):
            
            # Rule 1: Block editing a contact that is ALREADY China/Dubai Cargo
            for partner in self:
                if partner.contact_type in protected_types:
                    raise AccessError("Access Denied: Only Admin users can edit China Cargo or Dubai Cargo contacts.")
            
            # Rule 2: Block a user from CHANGING a normal customer INTO China/Dubai Cargo
            if vals.get('contact_type') in protected_types:
                raise AccessError("Access Denied: Only Admin users can classify a contact as China Cargo or Dubai Cargo.")

        return super().write(vals)

    # STRICT SECURITY: Block Deletes
    def unlink(self):
        protected_types = ['china_cargo', 'dubai_cargo']
        
        if not self.env.user.has_group('ogi_transit.group_ogi_admin'):
            for partner in self:
                if partner.contact_type in protected_types:
                    raise AccessError("Access Denied: Only Admin users can delete China Cargo or Dubai Cargo contacts.")
                    
        return super().unlink()
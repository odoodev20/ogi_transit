from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class OgiTransitVendorBill(models.Model):
    _name = 'ogi.transit.vendor.bill'
    _description = 'Vendor Bill'
    _inherit = ['mail.thread']

    name = fields.Char(string='Bill Reference', required=True, copy=False, readonly=True, default='Draft')
    
    # REMOVED: required=True from database level so "Other Supplier" bills can skip it
    container_id = fields.Many2one('ogi.transit.container', string='Container', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Vendor', required=True, tracking=True)

    # UPDATED: New Invoice Types
    expense_type = fields.Selection([
        ('freight', 'Freight (USD)'),
        ('customs', 'Customs Clearance (GNF)'),
        ('bgda', 'BGDA (GNF)'),
        ('other', 'Other Supplier')
    ], string='Invoice Type', required=True, tracking=True)

    currency = fields.Selection([('USD', 'USD'), ('GNF', 'GNF')], string='Currency', required=True, tracking=True)
    amount_total = fields.Float(string='Total Amount', required=True, tracking=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('issued', 'Approved'),
        ('paid', 'Paid')
    ], string='Status', default='draft', tracking=True)

    # NEW: Strictly enforce Container rules dynamically
    @api.constrains('expense_type', 'container_id')
    def _check_mandatory_container(self):
        for bill in self:
            if bill.expense_type in ['freight', 'customs', 'bgda'] and not bill.container_id:
                # REFACTORED: Exception wrapped in _()
                raise ValidationError(_("Validation Error: The Container field is mandatory for Freight, Customs Clearance, and BGDA invoices."))

    # NEW: Dynamic Auto-Assignment Engine
    @api.onchange('expense_type', 'container_id')
    def _onchange_expense_and_container(self):
        # 1. Handle Currency
        if self.expense_type == 'freight':
            self.currency = 'USD'
        elif self.expense_type in ['customs', 'bgda']:
            self.currency = 'GNF'

        # 2. Handle Dynamic Partner Auto-Assignment
        if self.expense_type and self.container_id:
            # Freight (USD) -> Find Cargo China or Cargo Dubai
            if self.expense_type == 'freight':
                if self.container_id.origin == 'china':
                    cargo = self.env['res.partner'].search([('contact_type', '=', 'china_cargo')], limit=1)
                    self.partner_id = cargo.id if cargo else False
                elif self.container_id.origin == 'dubai':
                    cargo = self.env['res.partner'].search([('contact_type', '=', 'dubai_cargo')], limit=1)
                    self.partner_id = cargo.id if cargo else False
            
            # Customs or BGDA -> Assigned Freight Forwarder
            elif self.expense_type in ['customs', 'bgda']:
                self.partner_id = self.container_id.forwarder_id.id if self.container_id.forwarder_id else False

        # If user clears expense type or selects 'other', wipe the partner so they can pick manually
        if self.expense_type == 'other' or not self.expense_type:
            self.partner_id = False

    def action_approve(self):
        for bill in self:
            if bill.name == 'Draft':
                seq = self.env['ir.sequence'].next_by_code('ogi.transit.vendor.bill') or '0000'
                year = fields.Date.today().year
                # REFACTORED: Used %s formatting. No _() needed here as it is a reference code.
                bill.name = "VB-%s-%s-%s" % (bill.currency, year, seq)
            bill.state = 'issued'

# ==========================================
# VENDOR PAYMENT WIZARD
# ==========================================
class OgiVendorPaymentWizard(models.TransientModel):
    _name = 'ogi.transit.vendor.payment.wizard'
    _description = 'Vendor Payout Wizard'

    bill_id = fields.Many2one('ogi.transit.vendor.bill', string='Vendor Bill', required=True)
    amount = fields.Float(related='bill_id.amount_total', string='Amount to Pay', readonly=True)
    currency = fields.Selection(related='bill_id.currency', string='Currency', readonly=True)
    
    cashbox_id = fields.Many2one(
        'ogi.transit.cashbox', 
        string='Withdraw From Register', 
        required=True, 
        domain="[('currency', '=', currency)]"
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            res['bill_id'] = self.env.context['active_id']
        return res

    def action_register_payout(self):
        if self.cashbox_id.balance < self.amount:
            # REFACTORED: Converted f-string to %s and wrapped in _()
            raise ValidationError(_("Insufficient Funds! The %s register only has %s %s available.") % (self.cashbox_id.name, self.cashbox_id.balance, self.currency))

        Transaction = self.env['ogi.transit.transaction']
        txn = Transaction.create({
            'cashbox_id': self.cashbox_id.id,
            'type': 'out',
            'amount': self.amount,
            'partner_id': self.bill_id.partner_id.id,
            # REFACTORED: Converted f-string to %s and wrapped in _()
            'reason': _("Payout for Vendor Bill: %s") % self.bill_id.name # FIXED: Changed 'reference' to 'reason'
        })
        txn.action_confirm() 

        self.bill_id.state = 'paid'
        # REFACTORED: Converted f-string to %s and wrapped in _()
        self.bill_id.message_post(body=_("<strong>Payout Registered</strong><br/>%s %s was withdrawn from %s.") % (self.amount, self.currency, self.cashbox_id.name))
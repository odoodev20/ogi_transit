from odoo import models, fields, api
from odoo.exceptions import ValidationError

class OgiTransitVendorBill(models.Model):
    _name = 'ogi.transit.vendor.bill'
    _description = 'Vendor Bill'
    _inherit = ['mail.thread']

    name = fields.Char(string='Bill Reference', required=True, copy=False, readonly=True, default='Draft')
    container_id = fields.Many2one('ogi.transit.container', string='Container', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Vendor', required=True, tracking=True)

    expense_type = fields.Selection([
        ('freight', 'Shipping Line / Freight (USD)'),
        ('customs', 'Customs Authority (GNF)'),
        ('forwarder', 'Freight Forwarder (GNF)'),
        ('other', 'Other Expenses')
    ], string='Expense Type', required=True, tracking=True)

    currency = fields.Selection([('USD', 'USD'), ('GNF', 'GNF')], string='Currency', required=True, tracking=True)
    amount_total = fields.Float(string='Total Amount', required=True, tracking=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('issued', 'Approved'),
        ('paid', 'Paid')
    ], string='Status', default='draft', tracking=True)

    # ==========================================
    # NEW: Auto-select currency based on Expense Type
    # ==========================================
    @api.onchange('expense_type')
    def _onchange_expense_type(self):
        if self.expense_type == 'freight':
            self.currency = 'USD'
        elif self.expense_type in ['customs', 'forwarder']:
            self.currency = 'GNF'

    def action_approve(self):
        for bill in self:
            if bill.name == 'Draft':
                seq = self.env['ir.sequence'].next_by_code('ogi.transit.vendor.bill') or '0000'
                year = fields.Date.today().year
                bill.name = f"VB-{bill.currency}-{year}-{seq}"
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

    # ==========================================
    # NEW: Forces the current bill's ID into the popup
    # ==========================================
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            res['bill_id'] = self.env.context['active_id']
        return res

    def action_register_payout(self):
        if self.cashbox_id.balance < self.amount:
            raise ValidationError(f"Insufficient Funds! The {self.cashbox_id.name} register only has {self.cashbox_id.balance} {self.currency} available.")

        Transaction = self.env['ogi.transit.transaction']
        txn = Transaction.create({
            'cashbox_id': self.cashbox_id.id,
            'type': 'out',
            'amount': self.amount,
            'partner_id': self.bill_id.partner_id.id,
            'reference': f"Payout for Vendor Bill: {self.bill_id.name}"
        })
        txn.action_confirm() 

        self.bill_id.state = 'paid'
        self.bill_id.message_post(body=f"<strong>Payout Registered</strong><br/>{self.amount} {self.currency} was withdrawn from {self.cashbox_id.name}.")
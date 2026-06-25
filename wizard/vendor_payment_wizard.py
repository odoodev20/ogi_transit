from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from markupsafe import Markup

class OgiTransitVendorPaymentWizard(models.TransientModel):
    _name = 'ogi.transit.vendor.payment.wizard'
    _description = 'Register Vendor Payout'

    bill_id = fields.Many2one('ogi.transit.vendor.bill', string='Vendor Bill', required=True)
    amount = fields.Float(string='Payout Amount', required=True)
    currency = fields.Selection(related='bill_id.currency', readonly=True)
    
    # NEW: Added Receipt Number field
    receipt_number = fields.Char(string='Receipt/Transfer ID', required=True)
    
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
            bill = self.env['ogi.transit.vendor.bill'].browse(self.env.context['active_id'])
            res['bill_id'] = bill.id
            res['amount'] = bill.amount_residual
        return res

    def action_register_payout(self):
        if self.amount <= 0:
            raise ValidationError(_("Payout amount must be strictly greater than zero."))
            
        if self.amount > self.bill_id.amount_residual:
            raise ValidationError(_("You cannot pay more than the remaining balance due."))

        if self.cashbox_id.balance < self.amount:
            raise ValidationError(_("Security Block: Insufficient Funds! You cannot pay %s. The %s register only has %s available.") % (self.amount, self.cashbox_id.name, self.cashbox_id.balance))
            
        Transaction = self.env['ogi.transit.transaction']
        txn = Transaction.create({
            'cashbox_id': self.cashbox_id.id,
            'type': 'out',
            'amount': self.amount,
            'partner_id': self.bill_id.partner_id.id,
            'reason': _("Vendor Payout: %s") % self.bill_id.name,
            'receipt_number': self.receipt_number,
            'is_wallet_transaction': False  # CRITICAL FIX: Stops the system from touching the deposit wallet
        })
        txn.action_confirm()
        
        self.bill_id.amount_paid += self.amount
        
        self.bill_id.message_post(body=Markup(_("<strong>Payout Registered</strong><br/>%s %s withdrawn from %s.<br/><strong>Receipt No:</strong> %s")) % (self.amount, self.currency, self.cashbox_id.name, self.receipt_number))
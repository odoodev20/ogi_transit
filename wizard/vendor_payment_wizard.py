from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from markupsafe import Markup

class OgiTransitVendorPaymentWizard(models.TransientModel):
    _name = 'ogi.transit.vendor.payment.wizard'
    _description = 'Register Vendor Payout'

    bill_id = fields.Many2one('ogi.transit.vendor.bill', string='Vendor Bill', required=True)
    amount = fields.Float(string='Payout Amount', required=True)
    currency = fields.Selection(related='bill_id.currency', readonly=True)
    
    cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Withdraw From Register', required=True, domain="[('currency', '=', currency)]")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            bill = self.env['ogi.transit.vendor.bill'].browse(self.env.context['active_id'])
            res['bill_id'] = bill.id
            res['amount'] = bill.amount_total
        return res

    def action_register_payout(self):
        if self.amount != self.bill_id.amount_total:
            # REFACTORED: Wrapped in _()
            raise ValidationError(_("Partial payments for vendors are restricted. Please pay the exact bill amount."))

        if self.cashbox_id.balance < self.amount:
            # REFACTORED: Converted f-string to %s and wrapped in _()
            raise ValidationError(_("Security Block: Insufficient Funds! You cannot pay %s. The %s register only has %s available.") % (self.amount, self.cashbox_id.name, self.cashbox_id.balance))

        Transaction = self.env['ogi.transit.transaction']
        txn = Transaction.create({
            'cashbox_id': self.cashbox_id.id,
            'type': 'out',
            'amount': self.amount,
            'partner_id': self.bill_id.partner_id.id,
            # REFACTORED: Converted f-string to %s and wrapped in _()
            'reason': _("Vendor Payout: %s") % self.bill_id.name
        })
        txn.action_confirm() 

        self.bill_id.state = 'paid'
        # REFACTORED: Converted f-string to %s and wrapped in _()
        self.bill_id.message_post(body=Markup(_("<strong>Payout Registered</strong><br/>%s %s withdrawn from %s.")) % (self.amount, self.currency, self.cashbox_id.name))
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from markupsafe import Markup

class OgiTransitVendorPaymentWizard(models.TransientModel):
    _name = 'ogi.transit.vendor.payment.wizard'
    _description = 'Register Vendor Payout'

    bill_id = fields.Many2one('ogi.transit.vendor.bill', string='Vendor Bill', required=True)
    amount = fields.Float(string='Payout Amount', required=True)
    currency = fields.Selection(related='bill_id.currency', readonly=True)
    
    # NEW: Supplier Advance Tracking
    is_wallet_payment = fields.Boolean(string='Pay using Supplier Advance')
    available_deposit = fields.Float(string='Available Advance', compute='_compute_available_deposit')

    receipt_number = fields.Char(string='Receipt/Transfer ID')
    
    cashbox_id = fields.Many2one(
        'ogi.transit.cashbox', 
        string='Withdraw From Register', 
        domain="[('currency', '=', currency), ('is_payment_method', '=', True)]"
    )
    
    cashbox_type = fields.Selection(related='cashbox_id.type_register', string='Register Type')
    
    payment_method_type = fields.Selection([
        ('cash', 'Cash'), ('deposit', 'Deposit'), ('transfer', 'Transfer'),
        ('cheque', 'Cheque'), ('mobile_money', 'Mobile Money')
    ], string='Payment Method')

    payment_method_bank = fields.Selection([
        ('deposit', 'Deposit'), ('transfer', 'Transfer'), ('cheque', 'Cheque')
    ], string='Payment Method')

    @api.depends('bill_id', 'currency')
    def _compute_available_deposit(self):
        for wiz in self:
            if wiz.bill_id and wiz.bill_id.partner_id:
                if wiz.currency == 'USD':
                    wiz.available_deposit = wiz.bill_id.partner_id.supplier_deposit_usd
                else:
                    wiz.available_deposit = wiz.bill_id.partner_id.supplier_deposit_gnf
            else:
                wiz.available_deposit = 0.0

    @api.onchange('cashbox_id', 'payment_method_bank')
    def _onchange_cashbox_type(self):
        if self.cashbox_type == 'cash':
            self.payment_method_type = 'cash'
        elif self.cashbox_type == 'mobile_money':
            self.payment_method_type = 'mobile_money'
        elif self.cashbox_type == 'bank':
            self.payment_method_type = self.payment_method_bank
        else:
            self.payment_method_type = False

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

        partner = self.bill_id.partner_id
        is_usd = self.currency == 'USD'
        Transaction = self.env['ogi.transit.transaction']

        # ===============================================
        # 1. HANDLE SUPPLIER ADVANCE PAYMENTS
        # ===============================================
        if self.is_wallet_payment:
            if self.amount > self.available_deposit:
                raise ValidationError(_("Insufficient advance! The supplier only has %s %s available.") % (self.available_deposit, self.currency))

            txn = Transaction.create({
                'cashbox_id': False,
                'type': 'out',
                'amount': self.amount,
                'partner_id': partner.id,
                'reason': _("Payout: Bill %s via Supplier Advance") % self.bill_id.name,
                'vendor_bill_id': self.bill_id.id,
                'receipt_number': _('ADVANCE-DEDUCTION'),
                'payment_method': 'Advance Balance',
                'payment_method_type': False,
                'is_wallet_transaction': False 
            })
            txn.action_confirm()
            
            if is_usd:
                partner.supplier_deposit_usd -= self.amount
            else:
                partner.supplier_deposit_gnf -= self.amount
                
            self.bill_id.amount_paid += self.amount
            self.bill_id._compute_amounts()
            self.bill_id.message_post(body=Markup(_("<strong>Advance Applied:</strong> %s %s deducted from supplier advance.")) % (self.amount, self.currency))
            return

        # ===============================================
        # 2. HANDLE STANDARD REGISTER PAYMENTS
        # ===============================================
        if not self.receipt_number:
            raise ValidationError(_("You must enter a Receipt/Transfer ID."))
        if not self.cashbox_id:
            raise ValidationError(_("You must select a Withdraw From Register."))

        if self.cashbox_type == 'bank':
            self.payment_method_type = self.payment_method_bank
            
        if not self.payment_method_type:
            raise ValidationError(_("You must select a Payment Method."))

        method_label = dict(self._fields['payment_method_type'].selection).get(self.payment_method_type) if self.payment_method_type else False
        
        txn = Transaction.create({
            'cashbox_id': self.cashbox_id.id,
            'type': 'out',
            'amount': self.amount,
            'partner_id': self.bill_id.partner_id.id,
            'payment_method_type': self.payment_method_type,
            'payment_method': method_label,
            'reason': _("Vendor Payout: %s") % self.bill_id.name,
            'receipt_number': self.receipt_number,
            'is_wallet_transaction': False,
            'vendor_bill_id': self.bill_id.id
        })
        
        txn.action_confirm()
        
        if txn.state == 'to_reconcile':
            self.bill_id.pending_txn_id = txn.id
            self.bill_id.state = 'to_reconcile'
            self.bill_id.message_post(body=_("<strong>Payment Pending Reconciliation</strong>"))
        else:
            self.bill_id.amount_paid += self.amount
            self.bill_id._compute_amounts()
            self.bill_id.message_post(body=Markup(_("<strong>Payout Registered</strong><br/>%s %s withdrawn from %s.<br/><strong>Receipt No:</strong> %s")) % (self.amount, self.currency, self.cashbox_id.name, self.receipt_number))
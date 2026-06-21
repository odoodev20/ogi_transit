from odoo import models, fields, api
from odoo.exceptions import ValidationError
from markupsafe import Markup

class OgiInvoicePaymentWizard(models.TransientModel):
    _name = 'ogi.invoice.payment.wizard'
    _description = 'Register Invoice Payment'

    invoice_id = fields.Many2one('ogi.transit.invoice', string='Invoice', required=True)
    amount = fields.Float(string='Payment Amount', required=True)
    payment_method = fields.Selection([
        ('cash', 'Cash'),
        ('mobile', 'Mobile Money'),
        ('check', 'Check'),
        ('deposit', 'Customer Deposit Wallet')
    ], string='Payment Method', required=True, default='cash')
    
    currency = fields.Selection(related='invoice_id.currency', string='Currency', readonly=True)
    amount_residual = fields.Float(related='invoice_id.amount_residual', string='Amount Due', readonly=True)
    
    # UPDATED: Now a computed field driven by Origin and Currency
    cashbox_id = fields.Many2one(
        'ogi.transit.cashbox', 
        string='Deposit Into Register', 
        compute='_compute_cashbox_id', 
        store=True
    )
    
    available_deposit = fields.Float(string='Available Wallet Balance', compute='_compute_available_deposit')

    # NEW: Automatically determine the correct register
    @api.depends('invoice_id', 'currency', 'payment_method')
    def _compute_cashbox_id(self):
        for wiz in self:
            if wiz.payment_method == 'deposit':
                wiz.cashbox_id = False
                continue

            if wiz.invoice_id and wiz.currency and wiz.invoice_id.container_id.origin:
                origin = wiz.invoice_id.container_id.origin
                currency = wiz.currency

                # Query the database for the exact matching vault
                cashbox = self.env['ogi.transit.cashbox'].search([
                    ('origin', '=', origin),
                    ('currency', '=', currency)
                ], limit=1)

                wiz.cashbox_id = cashbox.id if cashbox else False
            else:
                wiz.cashbox_id = False

    @api.depends('invoice_id', 'currency')
    def _compute_available_deposit(self):
        for wiz in self:
            if wiz.invoice_id and wiz.currency == 'USD':
                wiz.available_deposit = wiz.invoice_id.partner_id.deposit_usd
            elif wiz.invoice_id and wiz.currency == 'GNF':
                wiz.available_deposit = wiz.invoice_id.partner_id.deposit_gnf
            else:
                wiz.available_deposit = 0.0

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            invoice = self.env['ogi.transit.invoice'].browse(self.env.context['active_id'])
            res['invoice_id'] = invoice.id
            res['amount'] = invoice.amount_residual
        return res

    def action_register_payment(self):
        if self.amount <= 0:
            raise ValidationError("The payment amount must be strictly greater than zero.")
        
        partner = self.invoice_id.partner_id
        is_usd = self.currency == 'USD'

        # SCENARIO A: Paying with the Virtual Wallet
        if self.payment_method == 'deposit':
            if self.amount > self.amount_residual:
                raise ValidationError("You cannot apply more deposit than the invoice balance due.")
            
            if self.amount > self.available_deposit:
                raise ValidationError(f"Insufficient funds! The customer only has {self.available_deposit} {self.currency} in their wallet.")
            
            if is_usd:
                partner.deposit_usd -= self.amount
            else:
                partner.deposit_gnf -= self.amount
                
            self.invoice_id.amount_paid += self.amount
            self.invoice_id.message_post(body=Markup(f"<strong>Deposit Applied:</strong> {self.amount} {self.currency} deducted from customer wallet."))
            return

        # SCENARIO B: Paying with Physical Funds
        if not self.cashbox_id:
            # UPDATED: Better error message if the required vault doesn't exist
            origin_str = str(self.invoice_id.container_id.origin).capitalize()
            raise ValidationError(f"Configuration Error: Could not find an active '{origin_str} {self.currency}' Cash Register. Please create one in the Finance menu first.")

        payment_to_invoice = self.amount
        overpayment = 0.0

        if self.amount > self.amount_residual:
            payment_to_invoice = self.amount_residual
            overpayment = self.amount - self.amount_residual

        self.invoice_id.amount_paid += payment_to_invoice

        if overpayment > 0:
            if is_usd:
                partner.deposit_usd += overpayment
            else:
                partner.deposit_gnf += overpayment
            
            self.invoice_id.message_post(body=Markup(f"<strong>Overpayment Detected:</strong> {overpayment} {self.currency} added to Deposit Balance."))

        method_label = dict(self._fields['payment_method'].selection).get(self.payment_method)
        ref_text = f"Payment: Inv {self.invoice_id.name} ({payment_to_invoice})"
        
        if overpayment > 0:
            ref_text += f" + Deposit ({overpayment})"

        txn = self.env['ogi.transit.transaction'].create({
            'cashbox_id': self.cashbox_id.id,
            'type': 'in',
            'amount': self.amount,
            'partner_id': partner.id,
            'reason': f"{ref_text} via {method_label}"
        })
        
        txn.action_confirm()
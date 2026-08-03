from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from markupsafe import Markup
from odoo.tools import float_compare

class OgiInvoicePaymentWizard(models.TransientModel):
    _name = 'ogi.invoice.payment.wizard'
    _description = 'Register Invoice Payment'

    invoice_id = fields.Many2one('ogi.transit.invoice', string='Invoice', required=True)
    amount = fields.Float(string='Payment Amount', required=True)
    receipt_number = fields.Char(string='Receipt/Transfer ID')
    currency = fields.Selection(related='invoice_id.currency', string='Currency', readonly=True)
    amount_residual = fields.Float(related='invoice_id.amount_residual', string='Amount Due', readonly=True)
    available_deposit = fields.Float(string='Available Wallet Balance', compute='_compute_available_deposit')

    # NEW: Register and Payment Method Logic
    is_wallet_payment = fields.Boolean(string="Pay using Customer Deposit Wallet", default=False)
    
    cashbox_id = fields.Many2one(
        'ogi.transit.cashbox', 
        string='Deposit Into Register', 
        domain="[('currency', '=', currency), ('is_payment_method', '=', True)]"
    )
    cashbox_type = fields.Selection(related='cashbox_id.type_register', string='Register Type')
    
    payment_method_type = fields.Selection([
        ('cash', 'Cash'),
        ('deposit', 'Deposit'),
        ('transfer', 'Transfer'),
        ('cheque', 'Cheque'),
        ('mobile_money', 'Mobile Money')
    ], string='Payment Method')

    # NEW: UI-Specific field for Banks
    payment_method_bank = fields.Selection([
        ('deposit', 'Deposit'), ('transfer', 'Transfer'), ('cheque', 'Cheque')
    ], string='Payment Method')

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

    @api.onchange('cashbox_id')
    def _onchange_cashbox_type(self):
        if self.cashbox_type == 'cash':
            self.payment_method_type = 'cash'
        elif self.cashbox_type == 'mobile_money':
            self.payment_method_type = 'mobile_money'
        else:
            self.payment_method_type = False

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
            raise ValidationError(_("The payment amount must be strictly greater than zero."))
        
        partner = self.invoice_id.partner_id
        is_usd = self.currency == 'USD'

        # 1. HANDLE WALLET PAYMENTS (Goes straight to Done)
        if self.is_wallet_payment:
            if self.amount > self.amount_residual:
                raise ValidationError(_("You cannot apply more deposit than the invoice balance due."))
            if self.amount > self.available_deposit:
                raise ValidationError(_("Insufficient funds! The customer only has %s %s in their wallet.") % (self.available_deposit, self.currency))

            txn = self.env['ogi.transit.transaction'].create({
                'cashbox_id': False, 
                'type': 'in',
                'amount': self.amount,
                'partner_id': partner.id,
                'reason': _("Payment: Inv %s via Wallet Balance") % self.invoice_id.name,
                'invoice_id': self.invoice_id.id,
                'receipt_number': _('WALLET-DEDUCTION'),
                'payment_method': 'Wallet Balance',
                'payment_method_type': False,
                'is_wallet_transaction': False 
            })
            txn.action_confirm()
            
            if is_usd:
                partner.deposit_usd -= self.amount
            else:
                partner.deposit_gnf -= self.amount
                
            self.invoice_id.amount_paid += self.amount
            self.invoice_id.message_post(body=Markup(_("<strong>Deposit Applied:</strong> %s %s deducted from customer wallet.")) % (self.amount, self.currency))
            self.invoice_id._compute_amounts()
            return

        # 2. HANDLE STANDARD REGISTER PAYMENTS
        if not self.receipt_number:
            raise ValidationError(_("You must enter a 'Received/Sent Number' (Receipt/Transfer ID)."))
        if not self.cashbox_id:
            raise ValidationError(_("You must select a 'Deposit Into Register'."))
            
        # NEW: Sync the bank field
        if self.cashbox_type == 'bank':
            self.payment_method_type = self.payment_method_bank
            
        if not self.payment_method_type:
            raise ValidationError(_("You must select a Payment Method."))

        method_label = dict(self._fields['payment_method_type'].selection).get(self.payment_method_type)
        ref_text = _("Payment: Inv %s") % self.invoice_id.name

        txn = self.env['ogi.transit.transaction'].create({
            'cashbox_id': self.cashbox_id.id,
            'type': 'in',
            'amount': self.amount,
            'partner_id': partner.id,
            'reason': _("%s via %s") % (ref_text, method_label),
            'invoice_id': self.invoice_id.id,
            'receipt_number': self.receipt_number,
            'payment_method': method_label,
            # THE FIX: Explicitly passing the type to pass the validation rule
            'payment_method_type': self.payment_method_type,
            'is_wallet_transaction': False 
        })
        
        txn.action_confirm()

        # Workflow Routing
        if txn.state == 'to_reconcile':
            self.invoice_id.pending_txn_id = txn.id
            self.invoice_id.state = 'to_reconcile'
            self.invoice_id.message_post(body=Markup(_("<strong>Payment Pending Reconciliation</strong>")))
        else:
            self.invoice_id._process_validated_payment(txn.amount, self.currency, txn.payment_method)


# ==========================================
# NEW: INTER-LOAN REPAYMENT WIZARD
# ==========================================
class OgiLoanRepaymentWizard(models.TransientModel):
    _name = 'ogi.loan.repayment.wizard'
    _description = 'Register Loan Repayment'

    loan_id = fields.Many2one('ogi.transit.inter.cash.loan', string='Loan', required=True)
    amount = fields.Float(string='Repayment Amount', required=True)
    amount_residual = fields.Float(related='loan_id.amount_residual', string='Remaining Balance')
    receipt_number = fields.Char(string='Receipt/Transfer ID', required=True)
    currency = fields.Selection(related='loan_id.source_cashbox_id.currency')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            loan = self.env['ogi.transit.inter.cash.loan'].browse(self.env.context['active_id'])
            res['loan_id'] = loan.id
            res['amount'] = loan.amount_residual
        return res

    def action_register_repayment(self):
        if self.amount <= 0:
            raise ValidationError(_("Repayment amount must be strictly greater than zero."))
        
        # Safely check if repayment exceeds residual using float_compare
        if float_compare(self.amount, self.amount_residual, precision_digits=2) == 1:
            raise ValidationError(_("You cannot repay more than the remaining balance."))
        
        loan = self.loan_id
        if loan.dest_cashbox_id.balance < self.amount:
            raise ValidationError(_("Insufficient funds! %s does not have enough balance to make this repayment.") % (loan.dest_cashbox_id.name))

        Transaction = self.env['ogi.transit.transaction']
        
        # 1. Withdraw from destination register
        tx_out = Transaction.create({
            'cashbox_id': loan.dest_cashbox_id.id,
            'type': 'out',
            'amount': self.amount,
            'reason': _("Loan Repayment to %s") % (loan.source_cashbox_id.name),
            'receipt_number': self.receipt_number,
            'is_wallet_transaction': False
        })
        # ALWAYS call the action method to trigger internal validations
        tx_out.action_confirm() 
        
        # 2. Deposit into source register
        tx_in = Transaction.create({
            'cashbox_id': loan.source_cashbox_id.id,
            'type': 'in',
            'amount': self.amount,
            'reason': _("Loan Repayment from %s") % (loan.dest_cashbox_id.name),
            'receipt_number': self.receipt_number,
            'is_wallet_transaction': False
        })
        # ALWAYS call the action method to trigger internal validations
        tx_in.action_confirm() 
        
        # 3. Update loan math
        loan.amount_paid += self.amount
        
        # 4. Auto-switch state safely using float_compare
        if float_compare(loan.amount_paid, loan.amount, precision_digits=2) >= 0:
            loan.state = 'paid'
        else:
            loan.state = 'partial'
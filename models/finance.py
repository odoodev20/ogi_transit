from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, AccessError
from markupsafe import Markup


class OgiTransitCashbox(models.Model):
    _name = 'ogi.transit.cashbox'
    _description = 'Register (Bank, Cash, Mobile Money)' # UPDATED
    _inherit = ['mail.thread']
    
    name = fields.Char(string='Register Name', required=True)
    
    # NEW: Register Types and Payment Method Toggle
    type_register = fields.Selection([
        ('cash', 'Cash'), 
        ('bank', 'Bank'), 
        ('mobile_money', 'Mobile Money')
    ], string='Type', required=True, default='cash')
    
    is_payment_method = fields.Boolean(string='Payment Method', default=True)

    currency = fields.Selection([('USD', 'USD'), ('GNF', 'GNF')], string='Currency', required=True)
    
    # UPDATED: Removed required=True at the database level to support Bank/Mobile rules
    origin = fields.Selection([('china', 'China'), ('dubai', 'Dubai')], string='Origin')
    
    transaction_ids = fields.One2many('ogi.transit.transaction', 'cashbox_id', string='Transactions')
    balance = fields.Float(string='Current Balance', compute='_compute_balance', store=True)

    @api.constrains('type_register', 'origin')
    def _check_origin_requirement(self):
        for box in self:
            if box.type_register == 'cash' and not box.origin:
                raise ValidationError("Validation Error: The Origin field is mandatory when the Type is 'Cash'.")

    @api.depends('transaction_ids.amount', 'transaction_ids.type', 'transaction_ids.state')
    def _compute_balance(self):
        for box in self:
            valid_txs = box.transaction_ids.filtered(lambda t: t.state == 'done')
            total_in = sum(valid_txs.filtered(lambda t: t.type == 'in').mapped('amount'))
            total_out = sum(valid_txs.filtered(lambda t: t.type == 'out').mapped('amount'))
            box.balance = total_in - total_out

# ==========================================
# TRANSACTION & WALLET ENGINE
# ==========================================
class OgiTransitTransaction(models.Model):
    _name = 'ogi.transit.transaction'
    _description = 'Cash Transaction'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')

    cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Cash Register', required=False, ondelete='restrict', tracking=True)
    cashbox_type = fields.Selection(related='cashbox_id.type_register', string='Register Type', readonly=True)
    payment_method_type = fields.Selection([
        ('cash', 'Cash'),
        ('deposit', 'Deposit'),
        ('transfer', 'Transfer'),
        ('cheque', 'Cheque'),
        ('mobile_money', 'Mobile Money')
    ], string='Payment Method', tracking=True)

    # NEW: UI-Specific field for Banks
    payment_method_bank = fields.Selection([
        ('deposit', 'Deposit'), ('transfer', 'Transfer'), ('cheque', 'Cheque')
    ], string='Payment Method')

    # UPDATED ONCHANGE
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
            self.payment_method_bank = False


    type = fields.Selection([('in', 'Incoming (+)' ), ('out', 'Outgoing (-)')], string='Type', required=True, tracking=True)
    
    amount = fields.Float(string='Amount', required=True, tracking=True)
    currency = fields.Selection(related='cashbox_id.currency', string='Currency', readonly=True)
    date = fields.Datetime(string='Date', default=fields.Datetime.now, required=True, tracking=True)
    reason = fields.Char(string='Reason / Memo', required=True, tracking=True)
    
    # NEW: Receipt / Transfer Reference Number
    receipt_number = fields.Char(string='Received/Sent Number', tracking=True, help="External receipt, transfer ID, or reference number.")
    
    # Link to Customer Wallets
    partner_id = fields.Many2one('res.partner', string='Customer / Partner', tracking=True)
    is_wallet_transaction = fields.Boolean(string='Update Customer Wallet?', default=True, help="Check this to automatically increase/decrease the customer's deposit wallet.")

    invoice_id = fields.Many2one('ogi.transit.invoice', string='Related Invoice', readonly=True)

    # NEW: Link to Vendor Bills for the Payment History tab
    vendor_bill_id = fields.Many2one('ogi.transit.vendor.bill', string='Related Vendor Bill', readonly=True)
    
    # NEW: Store the exact method used
    payment_method = fields.Char(string='Method') 
    payment_method_display = fields.Char(string='Payment Method', compute='_compute_payment_method_display', store=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('to_reconcile', 'To Reconcile'), # NEW
        ('done', 'Done'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft', tracking=True)

    @api.depends('cashbox_id', 'payment_method_type', 'payment_method')
    def _compute_payment_method_display(self):
        for tx in self:
            if tx.payment_method_type:
                # Pulls the human-readable label from the Selection field
                method_label = dict(self._fields['payment_method_type'].selection).get(tx.payment_method_type)
                tx.payment_method_display = method_label
            elif tx.payment_method:
                tx.payment_method_display = tx.payment_method
            elif tx.cashbox_id:
                tx.payment_method_display = tx.cashbox_id.name
            else:
                tx.payment_method_display = 'Wallet Balance'

    @api.onchange('cashbox_id')
    def _onchange_cashbox_type(self):
        if self.cashbox_type == 'cash':
            self.payment_method_type = 'cash'
        elif self.cashbox_type == 'mobile_money':
            self.payment_method_type = 'mobile_money'
        elif self.cashbox_type == 'bank':
            # Auto-default to 'transfer' for better UX when a Bank is selected
            self.payment_method_type = 'transfer'
        else:
            self.payment_method_type = False

    @api.constrains('cashbox_id', 'payment_method_type')
    def _check_payment_method_rules(self):
        for tx in self:
            if tx.cashbox_id:
                if tx.cashbox_type == 'cash' and tx.payment_method_type != 'cash':
                    raise ValidationError(_("Validation Error: For Cash registers, the payment method must be 'Cash'."))
                elif tx.cashbox_type == 'mobile_money' and tx.payment_method_type != 'mobile_money':
                    raise ValidationError(_("Validation Error: For Mobile Money registers, the payment method must be 'Mobile Money'."))
                elif tx.cashbox_type == 'bank' and tx.payment_method_type not in ['deposit', 'transfer', 'cheque']:
                    raise ValidationError(_("Validation Error: For Bank registers, the payment method must be Deposit, Transfer, or Cheque."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Generate the sequence number
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.transaction') or 'New'
                
            # FIX: Auto-fill missing payment methods for system-generated background transfers
            if vals.get('cashbox_id') and not vals.get('payment_method_type'):
                cashbox = self.env['ogi.transit.cashbox'].browse(vals['cashbox_id'])
                if cashbox.type_register == 'cash':
                    vals['payment_method_type'] = 'cash'
                    vals['payment_method'] = 'Cash'
                elif cashbox.type_register == 'mobile_money':
                    vals['payment_method_type'] = 'mobile_money'
                    vals['payment_method'] = 'Mobile Money'
                elif cashbox.type_register == 'bank':
                    # Default internal bank transfers to 'Transfer'
                    vals['payment_method_type'] = 'transfer'
                    vals['payment_method'] = 'Transfer'
                    
        return super().create(vals_list)

    # 3. REPLACE `action_confirm` WITH THIS SPLIT WORKFLOW
    def action_confirm(self):
        for tx in self:
            if tx.cashbox_type == 'bank' and tx.payment_method_bank:
                tx.payment_method_type = tx.payment_method_bank
            if not tx.receipt_number:
                raise ValidationError(_("Validation Error: You must enter a 'Received/Sent Number'."))
            if tx.amount <= 0:
                raise ValidationError(_("The transaction amount must be strictly greater than zero."))
            
            # Workflow Split
            if tx.cashbox_id and tx.cashbox_id.type_register in ['bank', 'mobile_money']:
                tx.state = 'to_reconcile'
            else:
                tx._execute_financial_move()

    # 4. ADD THESE THREE NEW METHODS BELOW `action_confirm`
    def _execute_financial_move(self):
        for tx in self:
            if tx.type == 'out' and tx.cashbox_id:
                future_balance = tx.cashbox_id.balance - tx.amount
                if future_balance < 0:
                    raise ValidationError(_("Insufficient funds! You cannot withdraw %s. %s only has %s available.") % (tx.amount, tx.cashbox_id.name, tx.cashbox_id.balance))
                
            if tx.is_wallet_transaction and tx.partner_id:
                if tx.currency == 'USD':
                    if tx.type == 'in':
                        tx.partner_id.deposit_usd += tx.amount
                    elif tx.type == 'out':
                        if tx.partner_id.deposit_usd < tx.amount:
                            raise ValidationError(_("Wallet Error: %s only has %s USD.") % (tx.partner_id.name, tx.partner_id.deposit_usd))
                        tx.partner_id.deposit_usd -= tx.amount
                elif tx.currency == 'GNF':
                    if tx.type == 'in':
                        tx.partner_id.deposit_gnf += tx.amount
                    elif tx.type == 'out':
                        if tx.partner_id.deposit_gnf < tx.amount:
                            raise ValidationError(_("Wallet Error: %s only has %s GNF.") % (tx.partner_id.name, tx.partner_id.deposit_gnf))
                        tx.partner_id.deposit_gnf -= tx.amount
            tx.state = 'done'

    def action_reconcile_ok(self):
        for tx in self:
            # 1. Execute the standard transaction math to update the Cash Register
            tx._execute_financial_move()
            
            # 2. GUARANTEED SYNC: Update Customer Invoices directly via the linked ID
            if tx.invoice_id:
                # Pass payment_method_display to ensure a clean label is printed on the invoice
                tx.invoice_id._process_validated_payment(tx.amount, tx.currency, tx.payment_method_display)
                tx.invoice_id.pending_txn_id = False
                
            # Fallback search using sudo() for any edge cases
            invoices = self.env['ogi.transit.invoice'].sudo().search([('pending_txn_id', '=', tx.id)])
            for inv in invoices:
                if inv != tx.invoice_id:
                    inv._process_validated_payment(tx.amount, inv.currency, tx.payment_method_display)
                    inv.pending_txn_id = False
                    
            # 3. GUARANTEED SYNC: Update Vendor Bills using sudo() to bypass permission blocks
            bills = self.env['ogi.transit.vendor.bill'].sudo().search([('pending_txn_id', '=', tx.id)])
            for bill in bills:
                bill.amount_paid += tx.amount
                bill._compute_amounts()
                bill.pending_txn_id = False
                bill.message_post(body=Markup(_("<strong>Payout Reconciled via Ledger</strong><br/>%s %s was withdrawn.")) % (tx.amount, bill.currency))

    def action_reconcile_ko(self):
        for tx in self:
            # 1. Reset the Transaction UI and state
            if tx.cashbox_type == 'bank' and tx.payment_method_type:
                tx.payment_method_bank = tx.payment_method_type
            tx.state = 'draft'
            
            # 2. GUARANTEED SYNC: Reset Customer Invoices safely
            if tx.invoice_id:
                tx.invoice_id.pending_txn_id = False
                tx.invoice_id._compute_amounts()
                tx.invoice_id.message_post(body=Markup(_("<strong>Reconciliation Failed</strong><br/>The ledger transaction was rejected by Finance.")))
                
            invoices = self.env['ogi.transit.invoice'].sudo().search([('pending_txn_id', '=', tx.id)])
            for inv in invoices:
                if inv != tx.invoice_id:
                    inv.pending_txn_id = False
                    inv._compute_amounts()
                    inv.message_post(body=Markup(_("<strong>Reconciliation Failed</strong><br/>The ledger transaction was rejected by Finance.")))

            # 3. GUARANTEED SYNC: Reset Vendor Bills safely
            bills = self.env['ogi.transit.vendor.bill'].sudo().search([('pending_txn_id', '=', tx.id)])
            for bill in bills:
                bill.pending_txn_id = False
                bill._compute_amounts()
                bill.message_post(body=Markup(_("<strong>Reconciliation Failed</strong><br/>The ledger payout was rejected by Finance.")))

    def action_cancel(self):
        # Keeps your existing Reason Wizard logic intact
        return {
            'name': _('Mandatory Reason for Cancellation'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_transaction_id': self.id}
        }
    

# ==========================================
# NEW: DAILY CASH AUDIT (RECONCILIATION)
# ==========================================
class OgiTransitCashAudit(models.Model):
    _name = 'ogi.transit.cash.audit'
    _description = 'Daily Cash Audit'
    _inherit = ['mail.thread']

    name = fields.Char(string='Audit Reference', required=True, copy=False, default='New')
    cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Register to Audit', required=True, tracking=True)
    date = fields.Date(string='Audit Date', default=fields.Date.context_today, required=True)
    
    expected_balance = fields.Float(related='cashbox_id.balance', string='System Expected Balance', readonly=True)
    actual_counted = fields.Float(string='Physical Cash Counted', required=True, tracking=True)
    difference = fields.Float(string='Difference', compute='_compute_difference', store=True)
    
    notes = fields.Text(string='Audit Notes', tracking=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('validated', 'Validated')
    ], string='Status', default='draft', tracking=True)

    @api.depends('expected_balance', 'actual_counted')
    def _compute_difference(self):
        for audit in self:
            audit.difference = audit.actual_counted - audit.expected_balance

    def action_validate_audit(self):
        for audit in self:
            if audit.difference != 0 and not audit.notes:
                raise ValidationError(_("There is a cash discrepancy! You must provide an explanation in the Audit Notes before validating."))
            
            if audit.name == 'New':
                # Refactored f-string to a standard python format. No _() translation here to ensure references remain uniform across languages.
                audit.name = "AUDIT/%s/%s" % (audit.cashbox_id.name, audit.date)
            audit.state = 'validated'


# ==========================================
# 1. INTERNAL LOANS (Same Currency)
# ==========================================
class OgiTransitInterCashLoan(models.Model):
    _name = 'ogi.transit.inter.cash.loan'
    _description = 'Inter-Cashbox Loan'
    _inherit = ['mail.thread']

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    source_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Source Register', required=True, tracking=True)
    dest_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Destination Register', required=True, tracking=True)

    # NEW: Pull the currency from the source register to enforce UI rules
    currency = fields.Selection(related='source_cashbox_id.currency', string='Currency', readonly=True)
    
    amount = fields.Float(string='Amount', required=True, tracking=True)
    reason = fields.Char(string='Reason for Transfer', required=True, tracking=True)
    receipt_number = fields.Char(string='Transfer Receipt / Ref No.', required=True, tracking=True)
    
    # Destination Receipt Number removed as requested
    
    amount_paid = fields.Float(string='Amount Repaid', default=0.0, readonly=True, tracking=True)
    amount_residual = fields.Float(string='Remaining Balance', compute='_compute_residual', store=True)
    
    # Updated statuses for Loan workflow
    state = fields.Selection([
        ('draft', 'Draft'),
        ('validated', 'Validated'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid')
    ], string='Status', default='draft', tracking=True)

    @api.depends('amount', 'amount_paid')
    def _compute_residual(self):
        for loan in self:
            loan.amount_residual = loan.amount - loan.amount_paid

    @api.constrains('source_cashbox_id', 'dest_cashbox_id')
    def _check_valid_registers(self):
        for loan in self:
            if loan.source_cashbox_id and loan.dest_cashbox_id:
                # Rule 1: Cannot be the same register
                if loan.source_cashbox_id == loan.dest_cashbox_id:
                    raise ValidationError(_("Inter-Loan Between Same Register Forbidden. The source and destination registers cannot be identical."))
                # Rule 2: Must be the exact same currency
                if loan.source_cashbox_id.currency != loan.dest_cashbox_id.currency:
                    raise ValidationError(_("Currency Mismatch: Internal loans can only occur between registers with the same currency (e.g., USD to USD)."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.inter.cash.loan') or 'New'
        return super().create(vals_list)

    def action_validate_loan(self):
        for loan in self:
            if loan.state != 'draft':
                continue
                
            if loan.source_cashbox_id.balance < loan.amount:
                raise ValidationError(("Insufficient funds! The source register (%s) only has %s available.") % (loan.source_cashbox_id.name, loan.source_cashbox_id.balance))
            
            Transaction = self.env['ogi.transit.transaction']
            
            # 1. Debit Source Register (Sending Funds)
            Transaction.create({
                'cashbox_id': loan.source_cashbox_id.id,
                'type': 'out',
                'amount': loan.amount,
                'reason': ("Internal Loan to %s") % loan.dest_cashbox_id.name,
                'receipt_number': loan.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            # 2. Credit Destination Register (Receiving Funds)
            Transaction.create({
                'cashbox_id': loan.dest_cashbox_id.id,
                'type': 'in',
                'amount': loan.amount,
                'reason': ("Internal Loan from %s") % loan.source_cashbox_id.name,
                'receipt_number': loan.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            loan.state = 'validated'

    def action_register_repayment(self):
        self.ensure_one()
        return {
            'name': 'Register Loan Repayment',
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.loan.repayment.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_loan_id': self.id}
        }


# ==========================================
# 2. CARGO TRANSFERS (Internal Transfers)
# ==========================================
class OgiTransitCargoTransfer(models.Model):
    _name = 'ogi.transit.cargo.transfer'
    _description = 'CARGO Internal Transfer'
    _inherit = ['mail.thread']

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    source_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Source Register', required=True, tracking=True)
    dest_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Destination Register', required=True, tracking=True)
    
    amount = fields.Float(string='Amount', required=True, tracking=True)
    reason = fields.Char(string='Reason for Transfer', required=True, tracking=True)
    receipt_number = fields.Char(string='Transfer Receipt / Ref No.', required=True, tracking=True)
    
    # Step 2 verification field retained for Transfers
    destination_receipt_number = fields.Char(string='Destination Receipt No.', tracking=True)

    # Updated statuses for CARGO Transfer workflow
    state = fields.Selection([
        ('draft', 'Draft'),
        ('entrusted', 'Entrusted (In Transit)'),
        ('sent', 'Sent (Active)')
    ], string='Status', default='draft', tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.cargo.transfer') or 'New'
        return super().create(vals_list)

    def action_entrust(self):
        for transfer in self:
            if not transfer.receipt_number:
                raise ValidationError(_("Validation Error: You must enter a 'Transfer Receipt / Ref No.' before entrusting these funds."))
            if transfer.amount <= 0:
                raise ValidationError(_("The amount must be greater than zero."))
            if transfer.source_cashbox_id == transfer.dest_cashbox_id:
                raise ValidationError(_("Validation Error: The Source and Destination registers must be different."))
            if transfer.source_cashbox_id.balance < transfer.amount:
                raise ValidationError(_("Insufficient funds in %s to issue this transfer.") % transfer.source_cashbox_id.name)

            Transaction = self.env['ogi.transit.transaction']
            
            # Debit Source & Credit Destination instantly
            Transaction.create({
                'cashbox_id': transfer.source_cashbox_id.id,
                'type': 'out',
                'amount': transfer.amount,
                'reason': _("CARGO Transfer to %s") % (transfer.dest_cashbox_id.name),
                'receipt_number': transfer.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            Transaction.create({
                'cashbox_id': transfer.dest_cashbox_id.id,
                'type': 'in',
                'amount': transfer.amount,
                'reason': _("CARGO Transfer from %s") % (transfer.source_cashbox_id.name),
                'receipt_number': transfer.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            # Step 1 Complete: Funds are physically entrusted and in transit
            transfer.state = 'entrusted'

    def action_sent(self):
        for transfer in self:
            is_manager = self.env.user.has_group('ogi_transit.group_ogi_gerant')
            is_ceo = self.env.user.has_group('ogi_transit.group_ogi_pdg')
            is_admin = self.env.user.has_group('ogi_transit.group_ogi_admin')
            
            if not (is_manager or is_ceo or is_admin):
                raise AccessError(_("Access Denied: Only a Manager, CEO, or Admin can confirm that funds have securely arrived at their destination."))
            
            if not transfer.destination_receipt_number:
                raise ValidationError(_("Validation Error: You must enter the 'Destination Receipt No.' to prove the funds arrived."))
            
            transfer.state = 'sent'



# ==========================================
# INTERNAL TRANSFERS (Cash <-> Bank/Mobile)
# ==========================================
class OgiTransitInternalTransfer(models.Model):
    _name = 'ogi.transit.internal.transfer'
    _description = 'Internal Transfer'
    _inherit = ['mail.thread']

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    source_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Source Register', required=True, tracking=True)
    dest_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Destination Register', required=True, tracking=True)
    
    currency = fields.Selection(related='source_cashbox_id.currency', string='Currency', readonly=True)
    
    amount = fields.Float(string='Amount', required=True, tracking=True)
    reason = fields.Char(string='Reason / Memo', required=True, tracking=True)
    receipt_number = fields.Char(string='Receipt / Ref No.', required=True, tracking=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done')
    ], string='Status', default='draft', tracking=True)

    @api.constrains('source_cashbox_id', 'dest_cashbox_id')
    def _check_valid_registers(self):
        for transfer in self:
            if transfer.source_cashbox_id and transfer.dest_cashbox_id:
                if transfer.source_cashbox_id == transfer.dest_cashbox_id:
                    raise ValidationError(_("Validation Error: The source and destination registers cannot be identical."))
                if transfer.source_cashbox_id.currency != transfer.dest_cashbox_id.currency:
                    raise ValidationError(_("Currency Mismatch: Transfers can only occur between registers with the same currency (e.g., GNF to GNF)."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.internal.transfer') or 'New'
        return super().create(vals_list)

    def action_validate_transfer(self):
        for transfer in self:
            if transfer.state != 'draft':
                continue
                
            if transfer.source_cashbox_id.balance < transfer.amount:
                raise ValidationError(_("Insufficient funds! The source register (%s) only has %s available.") % (transfer.source_cashbox_id.name, transfer.source_cashbox_id.balance))
            
            if transfer.amount <= 0:
                raise ValidationError(_("The transfer amount must be strictly greater than zero."))
            
            Transaction = self.env['ogi.transit.transaction']
            
            # 1. Debit Source Register
            Transaction.create({
                'cashbox_id': transfer.source_cashbox_id.id,
                'type': 'out',
                'amount': transfer.amount,
                'reason': _("Internal Transfer to %s") % transfer.dest_cashbox_id.name,
                'receipt_number': transfer.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            # 2. Credit Destination Register
            Transaction.create({
                'cashbox_id': transfer.dest_cashbox_id.id,
                'type': 'in',
                'amount': transfer.amount,
                'reason': _("Internal Transfer from %s") % transfer.source_cashbox_id.name,
                'receipt_number': transfer.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            transfer.state = 'done'


# ==========================================
# INTERNAL LOAN REPAYMENT WIZARD
# ==========================================
class OgiTransitLoanRepaymentWizard(models.TransientModel):
    _name = 'ogi.transit.loan.repayment.wizard'
    _description = 'Loan Repayment Wizard'
    
    loan_id = fields.Many2one('ogi.transit.inter.cash.loan', string='Loan', required=True)
    amount = fields.Float(string='Repayment Amount', required=True)
    currency = fields.Selection(related='loan_id.currency', string='Currency', readonly=True)
    receipt_number = fields.Char(string='Transfer Ref / Receipt No.', required=True)
    
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self.env.context.get('active_id'):
            loan = self.env['ogi.transit.inter.cash.loan'].browse(self.env.context['active_id'])
            res['loan_id'] = loan.id
            res['amount'] = loan.amount_residual
        return res
        
    def action_confirm_repayment(self):
        if self.amount <= 0:
            raise ValidationError("The repayment amount must be strictly greater than zero.")
        if self.amount > self.loan_id.amount_residual:
            raise ValidationError(("You cannot repay more than the remaining balance (%s).") % self.loan_id.amount_residual)
        
        # When repaying, funds move from the Destination back to the Source.
        if self.loan_id.dest_cashbox_id.balance < self.amount:
            raise ValidationError(("Insufficient funds in %s to make this repayment.") % self.loan_id.dest_cashbox_id.name)
        
        Transaction = self.env['ogi.transit.transaction']
        
        # 1. Debit Destination Register (Returning funds)
        Transaction.create({
            'cashbox_id': self.loan_id.dest_cashbox_id.id,
            'type': 'out',
            'amount': self.amount,
            'reason': ("Loan Repayment to %s") % self.loan_id.source_cashbox_id.name,
            'receipt_number': self.receipt_number,
            'state': 'done',
            'is_wallet_transaction': False
        })
        
        # 2. Credit Source Register (Receiving repaid funds)
        Transaction.create({
            'cashbox_id': self.loan_id.source_cashbox_id.id,
            'type': 'in',
            'amount': self.amount,
            'reason': ("Loan Repayment from %s") % self.loan_id.dest_cashbox_id.name,
            'receipt_number': self.receipt_number,
            'state': 'done',
            'is_wallet_transaction': False
        })
        
        self.loan_id.amount_paid += self.amount
        
        # FIX: Dynamically determine if it's paid or partially paid
        if self.loan_id.amount_residual <= 0:
            self.loan_id.state = 'paid'
        elif self.loan_id.amount_paid > 0:
            self.loan_id.state = 'partial'
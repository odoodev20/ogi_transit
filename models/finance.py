from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, AccessError

class OgiTransitCashbox(models.Model):
    _name = 'ogi.transit.cashbox'
    _description = 'Physical Cash Register'
    _inherit = ['mail.thread']
    
    name = fields.Char(string='Cash Register Name', required=True)
    currency = fields.Selection([('USD', 'USD'), ('GNF', 'GNF')], string='Currency', required=True)
    origin = fields.Selection([('china', 'China'), ('dubai', 'Dubai')], string='Origin', required=True)
    
    transaction_ids = fields.One2many('ogi.transit.transaction', 'cashbox_id', string='Transactions')
    balance = fields.Float(string='Current Balance', compute='_compute_balance', store=True)

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
    cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Cash Register', required=True, ondelete='restrict', tracking=True)
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

    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft', tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.transaction') or 'New'
        return super().create(vals_list)

    def action_confirm(self):
        for tx in self:
            # NEW: Strict block if no receipt number is provided
            if not tx.receipt_number:
                raise ValidationError(_("Validation Error: You must enter a 'Received/Sent Number' (Receipt/Transfer ID) before confirming this transaction."))

            if tx.amount <= 0:
                raise ValidationError(_("The transaction amount must be strictly greater than zero."))
            
            # Strict balance check for outgoing funds from the cashbox
            if tx.type == 'out':
                future_balance = tx.cashbox_id.balance - tx.amount
                if future_balance < 0:
                    raise ValidationError(_("Insufficient funds! You cannot withdraw %s. The %s register only has %s available.") % (tx.amount, tx.cashbox_id.name, tx.cashbox_id.balance))
            
            # Customer Wallet Math Integration
            if tx.is_wallet_transaction and tx.partner_id:
                if tx.currency == 'USD':
                    if tx.type == 'in':
                        tx.partner_id.deposit_usd += tx.amount
                    elif tx.type == 'out':
                        if tx.partner_id.deposit_usd < tx.amount:
                            raise ValidationError(_("Wallet Error: %s only has %s USD in their deposit wallet.") % (tx.partner_id.name, tx.partner_id.deposit_usd))
                        tx.partner_id.deposit_usd -= tx.amount
                
                elif tx.currency == 'GNF':
                    if tx.type == 'in':
                        tx.partner_id.deposit_gnf += tx.amount
                    elif tx.type == 'out':
                        if tx.partner_id.deposit_gnf < tx.amount:
                            raise ValidationError(_("Wallet Error: %s only has %s GNF in their deposit wallet.") % (tx.partner_id.name, tx.partner_id.deposit_gnf))
                        tx.partner_id.deposit_gnf -= tx.amount

            tx.state = 'done'

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
# NEW: INTER-CASH LOAN / INTERNAL TRANSFER ENGINE
# ==========================================
class OgiTransitInterCashLoan(models.Model):
    _name = 'ogi.transit.inter.cash.loan'
    _description = 'Inter-Cashbox Transfer'
    _inherit = ['mail.thread']

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default='New')
    source_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Source Register', required=True, tracking=True)
    dest_cashbox_id = fields.Many2one('ogi.transit.cashbox', string='Destination Register', required=True, tracking=True)
    
    amount = fields.Float(string='Amount', required=True, tracking=True)
    reason = fields.Char(string='Reason for Transfer', required=True, tracking=True)
    
    receipt_number = fields.Char(string='Transfer Receipt / Ref No.', required=True, tracking=True)
    
    # NEW: Step 2 verification field
    destination_receipt_number = fields.Char(string='Destination Receipt No.', tracking=True)

    # ADD: New fields for partial payment tracking
    amount_paid = fields.Float(string='Amount Repaid', default=0.0, tracking=True)
    amount_residual = fields.Float(string='Remaining Balance', compute='_compute_residual', store=True)
    
    # UPDATED: Statuses split into Entrusted (In Transit) and Sent (At Destination)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('entrusted', 'Entrusted (In Transit)'),
        ('sent', 'Sent (Active)'),
        ('partial', 'Partially Paid'), # NEW
        ('paid', 'Paid')               # REPLACED 'repaid'
    ], string='Status', default='draft', tracking=True)

    # ADD: The compute method for the remaining balance
    @api.depends('amount', 'amount_paid')
    def _compute_residual(self):
        for loan in self:
            loan.amount_residual = loan.amount - loan.amount_paid

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.inter.cash.loan') or 'New'
        return super().create(vals_list)

    def action_confirm(self):
        for loan in self:
            # BUG FIX: Strict backend validation for the Sending Receipt Number
            if not loan.receipt_number:
                raise ValidationError(_("Validation Error: You must enter a 'Transfer Receipt / Ref No.' before entrusting these funds."))
                
            if loan.amount <= 0:
                raise ValidationError(_("The amount must be greater than zero."))
            if loan.source_cashbox_id == loan.dest_cashbox_id:
                raise ValidationError(_("Validation Error: The Source and Destination registers must be different."))
            if loan.source_cashbox_id.currency != loan.dest_cashbox_id.currency:
                raise ValidationError(_("Validation Error: Cross-currency transfers are strictly prohibited."))
            if loan.source_cashbox_id.balance < loan.amount:
                raise ValidationError(_("Insufficient funds in %s to issue this transfer.") % loan.source_cashbox_id.name)

            Transaction = self.env['ogi.transit.transaction']
            
            # Debit Source & Credit Destination instantly
            Transaction.create({
                'cashbox_id': loan.source_cashbox_id.id,
                'type': 'out',
                'amount': loan.amount,
                'reason': _("Transfer to %s") % (loan.dest_cashbox_id.name),
                'receipt_number': loan.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            Transaction.create({
                'cashbox_id': loan.dest_cashbox_id.id,
                'type': 'in',
                'amount': loan.amount,
                'reason': _("Transfer from %s") % (loan.source_cashbox_id.name),
                'receipt_number': loan.receipt_number,
                'state': 'done',
                'is_wallet_transaction': False
            })
            
            # Step 1 Complete: Funds are physically entrusted and in transit
            loan.state = 'entrusted'

    # NEW: Strict Step 2 execution
    def action_mark_sent(self):
        for loan in self:
            # Strictly enforce US 9.3 Rights and Permissions
            is_manager = self.env.user.has_group('ogi_transit.group_ogi_gerant')
            is_ceo = self.env.user.has_group('ogi_transit.group_ogi_pdg')
            
            if not (is_manager or is_ceo):
                raise AccessError(_("Access Denied: Only a Manager or CEO can confirm that funds have securely arrived at their destination."))
            
            if not loan.destination_receipt_number:
                raise ValidationError(_("Validation Error: You must enter the 'Destination Receipt No.' to prove the funds arrived."))
            
            loan.state = 'sent'
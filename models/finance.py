from odoo import models, fields, api
from odoo.exceptions import ValidationError

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
    
    # NEW: Link to Customer Wallets
    partner_id = fields.Many2one('res.partner', string='Customer', tracking=True)
    is_wallet_transaction = fields.Boolean(string='Update Customer Wallet?', help="Check this to automatically increase/decrease the customer's deposit wallet.")

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
            if tx.amount <= 0:
                raise ValidationError("The transaction amount must be strictly greater than zero.")
            
            # Strict balance check for outgoing funds from the cashbox
            if tx.type == 'out':
                future_balance = tx.cashbox_id.balance - tx.amount
                if future_balance < 0:
                    raise ValidationError(f"Insufficient funds! You cannot withdraw {tx.amount}. The {tx.cashbox_id.name} register only has {tx.cashbox_id.balance} available.")
            
            # NEW: Customer Wallet Math Integration
            if tx.is_wallet_transaction and tx.partner_id:
                if tx.currency == 'USD':
                    if tx.type == 'in':
                        tx.partner_id.deposit_usd += tx.amount
                    elif tx.type == 'out':
                        if tx.partner_id.deposit_usd < tx.amount:
                            raise ValidationError(f"Wallet Error: {tx.partner_id.name} only has {tx.partner_id.deposit_usd} USD in their deposit wallet.")
                        tx.partner_id.deposit_usd -= tx.amount
                
                elif tx.currency == 'GNF':
                    if tx.type == 'in':
                        tx.partner_id.deposit_gnf += tx.amount
                    elif tx.type == 'out':
                        if tx.partner_id.deposit_gnf < tx.amount:
                            raise ValidationError(f"Wallet Error: {tx.partner_id.name} only has {tx.partner_id.deposit_gnf} GNF in their deposit wallet.")
                        tx.partner_id.deposit_gnf -= tx.amount

            tx.state = 'done'

    def action_cancel(self):
        # Keeps your existing Reason Wizard logic intact
        return {
            'name': 'Mandatory Reason for Cancellation',
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
                raise ValidationError("There is a cash discrepancy! You must provide an explanation in the Audit Notes before validating.")
            
            if audit.name == 'New':
                audit.name = f"AUDIT/{audit.cashbox_id.name}/{audit.date}"
            audit.state = 'validated'
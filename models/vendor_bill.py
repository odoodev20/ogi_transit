from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from markupsafe import Markup

class OgiTransitVendorBill(models.Model):
    _name = 'ogi.transit.vendor.bill'
    _description = 'Vendor Bill'
    _inherit = ['mail.thread']

    name = fields.Char(string='Bill Reference', required=True, copy=False, readonly=True, default='Draft')
    container_id = fields.Many2one('ogi.transit.container', string='Container', tracking=True)
    partner_id = fields.Many2one('res.partner', string='Vendor', required=True, tracking=True)

    expense_type = fields.Selection([
        ('freight', 'Freight (USD)'),
        ('customs', 'Customs Clearance (GNF)'),
        ('bgda', 'BGDA (GNF)'),
        ('other', 'Other Supplier')
    ], string='Invoice Type', required=True, tracking=True)
    description = fields.Text(string='Description', tracking=True)

    currency = fields.Selection([('USD', 'USD'), ('GNF', 'GNF')], string='Currency', required=True, tracking=True)
    amount_total = fields.Float(string='Total Amount', required=True, tracking=True)
    # NEW: Breakdown fields for Freight Forwarder automated bills
    bgda_amount = fields.Float(string='Total BGDA', readonly=True, tracking=True)
    freight_forwarder_cost = fields.Float(string='Freight Forwarder Cost (GNF)', readonly=True, tracking=True)
    amount_paid = fields.Float(string='Amount Paid', default=0.0, tracking=True, readonly=True)
    amount_residual = fields.Float(string='Balance Due', compute='_compute_amounts', store=True)

    pending_txn_id = fields.Many2one('ogi.transit.transaction', string='Pending Payment Transaction', readonly=True)
    
    # NEW: One2many relationship to pull the Payment History
    transaction_ids = fields.One2many('ogi.transit.transaction', 'vendor_bill_id', string='Payment History', readonly=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('issued', 'Approved'),
        ('to_reconcile', 'To Reconcile'), 
        ('partial', 'Partially Paid'),
        ('paid', 'Paid')
    ], string='Status', default='draft', tracking=True)

    @api.depends('amount_total', 'amount_paid')
    def _compute_amounts(self):
        for bill in self:
            bill.amount_residual = bill.amount_total - bill.amount_paid
            if bill.state not in ['draft']:
                if bill.amount_residual <= 0 and bill.amount_total > 0:
                    bill.state = 'paid'
                elif bill.amount_paid > 0:
                    bill.state = 'partial'
                else:
                    bill.state = 'issued'

    @api.constrains('expense_type', 'container_id')
    def _check_mandatory_container(self):
        for bill in self:
            if bill.expense_type in ['freight', 'customs', 'bgda'] and not bill.container_id:
                raise ValidationError(_("Validation Error: The Container field is mandatory for Freight, Customs Clearance, and BGDA invoices."))

    @api.onchange('expense_type', 'container_id')
    def onchange_expense_and_container(self):
        res_domain = {'partner_id': []}
        
        if self.expense_type == 'freight':
            self.currency = 'USD'
        elif self.expense_type in ['customs', 'bgda']:
            self.currency = 'GNF'

        if self.expense_type == 'other' or not self.expense_type:
            self.partner_id = False
            
        elif self.expense_type and self.container_id:
            if self.expense_type == 'freight':
                if self.container_id.origin == 'china':
                    cargo = self.env['res.partner'].search([('contact_type', '=', 'china_cargo')], limit=1)
                    self.partner_id = cargo if cargo else False
                    res_domain['partner_id'] = [('contact_type', '=', 'china_cargo')]
                    
                elif self.container_id.origin == 'dubai':
                    cargo = self.env['res.partner'].search([('contact_type', '=', 'dubai_cargo')], limit=1)
                    self.partner_id = cargo if cargo else False
                    res_domain['partner_id'] = [('contact_type', '=', 'dubai_cargo')]
                    
            elif self.expense_type in ['customs', 'bgda']:
                self.partner_id = self.container_id.forwarder_id if self.container_id.forwarder_id else False

        return {'domain': res_domain}

    def action_reconcile_ok(self):
        for bill in self:
            if bill.pending_txn_id:
                bill.pending_txn_id._execute_financial_move()
                # THE FIX: Apply the money to the bill before clearing the pending ID
                bill.amount_paid += bill.pending_txn_id.amount
                bill.pending_txn_id = False
                
            bill._compute_amounts()
            bill.message_post(body=Markup(_("<strong>Payment Applied</strong> successfully.")))

    def action_reconcile_ko(self):
        for bill in self:
            if bill.pending_txn_id:
                bill.pending_txn_id.state = 'cancelled'
                bill.pending_txn_id = False
            bill.state = 'issued'

    def action_approve(self):
        for bill in self:
            if bill.name == 'Draft':
                seq = self.env['ir.sequence'].next_by_code('ogi.transit.vendor.bill') or '0000'
                year = fields.Date.today().year
                bill.name = "VB-%s-%s-%s" % (bill.currency, year, seq)
            bill.state = 'issued'
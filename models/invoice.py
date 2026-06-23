from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class CrmLead(models.Model):
    _inherit = 'crm.lead'
    ogi_invoice_id = fields.Many2one('ogi.transit.invoice', string='Linked Transit Invoice', readonly=True)

class OgiTransitInvoice(models.Model):
    _name = 'ogi.transit.invoice'
    _description = 'Customer Invoice'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Invoice No.', required=True, copy=False, readonly=True, default='Draft')
    container_id = fields.Many2one('ogi.transit.container', string='Container', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    
    # ==========================================
    # CRM & COLLECTION FIELDS
    # ==========================================
    crm_lead_ids = fields.One2many('crm.lead', 'ogi_invoice_id', string='CRM Follow-ups')
    crm_lead_count = fields.Integer(compute='_compute_crm_lead_count')
    
    last_call_date = fields.Date(string='Last Collection Call', tracking=True)
    promise_to_pay_date = fields.Date(string='Promise to Pay Date', tracking=True)
    collection_notes = fields.Text(string='Collection Notes')
    
    invoice_type = fields.Selection([
        ('fcl_usd', 'FCL Freight (USD)'),
        ('fcl_gnf', 'FCL Customs Clearance (GNF)'),
        ('lcl_usd', 'LCL Freight (USD)'),
        ('lcl_gnf', 'LCL Customs Clearance (GNF)')
    ], string='Invoice Type', required=True, tracking=True)
    
    currency = fields.Selection([('USD', 'USD'), ('GNF', 'GNF')], string='Currency', required=True, tracking=True)
    goods_description = fields.Char(string='Description of Goods', tracking=True)
    bgda_amount = fields.Float(string='BGDA Amount', tracking=True, default=0.0)
    
    amount_total = fields.Float(string='Total Amount', required=True, tracking=True)
    amount_paid = fields.Float(string='Amount Paid', default=0.0, readonly=True, tracking=True)
    amount_residual = fields.Float(string='Balance Due', compute='_compute_amounts', store=True)
    base_amount = fields.Float(string='Transit / Customs Amount', compute='_compute_base_amount', store=True)

    state = fields.Selection([
        ('draft', 'DRAFT'),
        ('issued', 'ISSUED'),
        ('partial', 'PARTIALLY PAID'),
        ('paid', 'PAID IN FULL'),
        ('canceled', 'CANCELED')
    ], string='Status', default='draft', tracking=True)

    @api.depends('crm_lead_ids')
    def _compute_crm_lead_count(self):
        for inv in self:
            inv.crm_lead_count = len(inv.crm_lead_ids)

    def action_create_crm_opportunity(self):
        self.ensure_one()
        return {
            'name': 'Create CRM Follow-up',
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'view_mode': 'form',
            'context': {
                'default_name': f'Collection: {self.partner_id.name} ({self.name})',
                'default_partner_id': self.partner_id.id,
                'default_ogi_invoice_id': self.id,
                'default_type': 'opportunity',
                'default_expected_revenue': self.amount_residual if self.currency == 'USD' else 0.0,
            }
        }

    def action_view_crm_leads(self):
        self.ensure_one()
        return {
            'name': 'Collection Follow-ups',
            'type': 'ir.actions.act_window',
            'res_model': 'crm.lead',
            'view_mode': 'list,form',
            'domain': [('ogi_invoice_id', '=', self.id)],
            'context': {'default_ogi_invoice_id': self.id}
        }

    @api.depends('amount_total', 'bgda_amount')
    def _compute_base_amount(self):
        for inv in self:
            inv.base_amount = inv.amount_total - inv.bgda_amount

    @api.depends('amount_total', 'amount_paid')
    def _compute_amounts(self):
        for inv in self:
            inv.amount_residual = inv.amount_total - inv.amount_paid
            if inv.state not in ['draft', 'canceled']:
                if inv.amount_residual <= 0 and inv.amount_total > 0:
                    inv.state = 'paid'
                elif inv.amount_paid > 0:
                    inv.state = 'partial'

    def action_issue(self):
        for inv in self:
            if inv.state != 'draft':
                continue
                
            # NEW: Block issuing GNF invoices if no forwarder is assigned
            if inv.currency == 'GNF' and inv.container_id and not inv.container_id.forwarder_id:
                raise ValidationError(
                    "Validation Error: You cannot issue a GNF invoice because no Freight Forwarder (Transitaire) is assigned to the container. "
                    "Please ask Logistics to assign one first."
                )

            if inv.name == 'Draft':
                type_prefix = 'FCL' if 'fcl' in inv.invoice_type else 'LCL'
                origin_code = 'CHI' if inv.container_id.bl_id.lot_id.origin == 'china' else 'DUB'
                seq = self.env['ir.sequence'].next_by_code('ogi.transit.invoice') or '00000'
                year = fields.Date.today().year
                inv.name = f"{type_prefix}-{inv.currency}-{origin_code}-{year}-{seq}"
            inv.state = 'issued'

    def action_cancel(self):
        self.ensure_one()
        
        # REMOVED: The Validation Error that previously blocked paid invoices.
        # The system will now allow the cancellation and automatically refund 
        # the amount_paid via the Reason Wizard.
        
        # Launch the mandatory reason popup
        return {
            'name': 'Mandatory Reason for Cancellation',
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_invoice_id': self.id}
        }
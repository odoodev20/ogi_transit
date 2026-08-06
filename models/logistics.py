import re
import math
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from odoo.tools import float_round
from markupsafe import Markup

# ==========================================
# NEW REPOSITORY MODELS (Screen 1)
# ==========================================
class OgiTransitShippingCompany(models.Model):
    _name = 'ogi.transit.shipping.company'
    _description = 'Shipping Company Repository'
    
    name = fields.Char(string='Company Name', required=True)

class OgiTransitPort(models.Model):
    _name = 'ogi.transit.port'
    _description = 'Port Repository'
    
    name = fields.Char(string='Port Name', required=True)
    origin = fields.Selection([
        ('china', 'China'),
        ('dubai', 'Dubai'),
        ('guinea', 'Guinea (Destination)') # NEW: Allows configuration of arrival ports
    ], string='Country / Region', required=True)

# ==========================================
# NEW: GOODS LOCATION REPOSITORY
# ==========================================
class OgiTransitLocation(models.Model):
    _name = 'ogi.transit.location'
    _description = 'Goods Location'
    _inherit = ['mail.thread']

    name = fields.Char(string='Location Name', required=True, tracking=True)
    type = fields.Selection([
        ('store', 'Store'),
        ('warehouse', 'Warehouse')
    ], string='Location Type', required=True, default='warehouse', tracking=True)


# ==========================================
# NEW DELIVERY NOTE MODEL
# ==========================================
class OgiTransitDeliveryNote(models.Model):
    _name = 'ogi.transit.delivery.note'
    _description = 'Delivery Note'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Reference', required=True, copy=False, default='Draft', tracking=True)
    
    pl_line_id = fields.Many2one('ogi.transit.pl.line', string='Packing List Line', ondelete='cascade')
    container_id = fields.Many2one('ogi.transit.container', string='Container', required=True, tracking=True)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    
    logistics_status = fields.Selection([
        ('pending', 'Pending at Port'),
        ('unpacked', 'Unpacked (Depoting)'),
        ('storage', 'In Storage'),
        ('retrieved', 'Retrieved by Customer')
    ], string='Logistics Status', default='pending', tracking=True)
    
    operator_note = fields.Text(string='Delivery Notes / Comments', tracking=True)

    # ==========================================
    # NEW: Payment Tracking & Authorization Fields
    # ==========================================
    # 1. BUG FIX: Removed `store=True` so it always calculates live
    payment_status = fields.Selection([
        ('unpaid', 'Unpaid'),
        ('partially_paid', 'Partially Paid'),
        ('paid', 'Paid')
    ], string='Payment Status', compute='_compute_payment_status')

    is_authorized = fields.Boolean(string="Authorized for Unpaid Delivery", default=False, tracking=True, copy=False)
    authorization_reason = fields.Char(string="Authorization Reason", tracking=True, copy=False)
    authorized_by_id = fields.Many2one('res.users', string="Authorized By", tracking=True, copy=False)

    
    location_id = fields.Many2one('ogi.transit.location', string='Storage Location', tracking=True)

    
    goods_description = fields.Char(string='Description of Goods', compute='_compute_goods_description', store=True)
    
    
    @api.depends('pl_line_id', 'container_id')
    def _compute_goods_description(self):
        for note in self:
            if note.pl_line_id:
                note.goods_description = note.pl_line_id.goods_description
            elif note.container_id and note.container_id.type in ['fcl_awaye', 'fcl_home']:
                note.goods_description = note.container_id.goods_description
            else:
                note.goods_description = ""

    @api.depends('container_id', 'partner_id')
    # 2. BUG FIX: Removed `@api.depends` to force real-time evaluation
    def _compute_payment_status(self):
        for note in self:
            invoices = self.env['ogi.transit.invoice'].search([
                ('container_id', '=', note.container_id.id),
                ('partner_id', '=', note.partner_id.id),
                ('state', '!=', 'canceled')
            ])
            
            if not invoices:
                note.payment_status = 'unpaid'
                continue

            total_amount = sum(invoices.mapped('amount_total'))
            total_paid = sum(invoices.mapped('amount_paid'))

            if total_paid >= total_amount and total_amount > 0:
                note.payment_status = 'paid'
            elif total_paid > 0:
                note.payment_status = 'partially_paid'
            else:
                note.payment_status = 'unpaid'

    def action_authorize_unpaid(self):
        self.ensure_one()
        # Launch the mandatory reason popup for Managers
        return {
            # REFACTORED: Window action wrapped in _()
            'name': _('Authorize Unpaid Delivery'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.reason.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_delivery_id': self.id}
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') in ('Draft', 'New Note'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.delivery.note') or 'BL-ERROR'
        return super(OgiTransitDeliveryNote, self).create(vals_list)

    
    def write(self, vals):
        for note in self:
            # 1. Enforce Location rule for 'storage' (Stockage)
            effective_status = vals.get('logistics_status', note.logistics_status)
            effective_location = vals.get('location_id', note.location_id.id)

            if effective_status == 'storage' and not effective_location:
                raise ValidationError(_("Validation Error: You must select a Storage Location before moving the delivery note to 'In Storage' (Stockage)."))
            
            # 2. BUG FIX: Bulletproof Security Control using sudo()
            if 'logistics_status' in vals and vals['logistics_status'] == 'retrieved':
                # Use sudo() to guarantee we see all invoices accurately regardless of Operator's rights
                invoices = self.env['ogi.transit.invoice'].sudo().search([
                    ('container_id', '=', note.container_id.id),
                    ('partner_id', '=', note.partner_id.id),
                    ('state', '!=', 'canceled')
                ])

                total_amount = sum(invoices.mapped('amount_total'))
                total_paid = sum(invoices.mapped('amount_paid'))
                is_fully_paid = (total_paid >= total_amount and total_amount > 0)

                is_auth = vals.get('is_authorized', note.is_authorized)

                if not is_fully_paid and not is_auth:
                    raise ValidationError(_("Security Control: You cannot deliver goods that are 'Unpaid' or 'Partially Paid'. A Manager must authorize this exception first."))
                    
        return super(OgiTransitDeliveryNote, self).write(vals)

# ==========================================
# INHERITS FOR EXISTING MODELS
# ==========================================
class OgiTransitPlLine(models.Model):
    _inherit = 'ogi.transit.pl.line'
    
    name = fields.Char(string='Line Reference', compute='_compute_name', store=True)
    delivery_note_id = fields.Many2one('ogi.transit.delivery.note', string='Delivery Note', readonly=True)

    # NEW: Positive value validation for Packing List lines
    @api.constrains('qty', 'ins_cbm', 'bgda')
    def _check_positive_pl_values(self):
        for line in self:
            if line.qty < 0:
                raise ValidationError(_("Validation Error: QTY cannot be a negative value."))
            if line.ins_cbm < 0:
                raise ValidationError(_("Validation Error: INS CBM cannot be a negative value."))
            if line.bgda < 0:
                raise ValidationError(_("Validation Error: BGDA cannot be a negative value."))

    @api.depends('partner_id.name', 'container_id.name')
    def _compute_name(self):
        for line in self:
            if line.partner_id and line.container_id:
                line.name = "%s - %s" % (line.partner_id.name, line.container_id.name)
            else:
                line.name = "New Line"

class OgiTransitInvoice(models.Model):
    _inherit = 'ogi.transit.invoice'
    
    goods_description = fields.Char(string='Description of Goods')
    bgda_amount = fields.Float(string='BGDA Amount')
    
    # NEW: Track Inspection Fees on the Invoice
    ins_amount = fields.Float(string='Inspection Fees (USD)')

# ==========================================
# CORE LOGISTICS MODELS
# ==========================================
class OgiTransitLot(models.Model):
    _name = 'ogi.transit.lot'
    _description = 'Transit Lot'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Lot No.', required=True, tracking=True, copy=False)
    origin = fields.Selection([
        ('china', 'China'),
        ('dubai', 'Dubai')
    ], string='Origin', required=True, tracking=True)
    date = fields.Date(string='Date', default=fields.Date.context_today, required=True)
    comment = fields.Text(string='Comment')
    bl_ids = fields.One2many('ogi.transit.bl', 'lot_id', string='Bills of Lading')

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Validation Error: The Lot Number must be unique!')
    ]

    @api.constrains('name')
    def _check_unique_lot_name(self):
        for lot in self:
            if lot.name:
                duplicate = self.search([
                    ('name', '=ilike', lot.name), 
                    ('id', '!=', lot.id)
                ], limit=1)
                if duplicate:
                    # REFACTORED: Exception wrapped in _()
                    raise ValidationError(_("Validation Error: The Lot Number must be unique! A lot with this number already exists in the system."))

    def write(self, vals):
        if 'origin' in vals:
            for lot in self:
                if lot.origin and lot.origin != vals['origin']:
                    Container = self.env['ogi.transit.container'].sudo()
                    domain = [('state', '=', 'released')]
                    
                    if 'lot_id' in Container._fields:
                        domain.append(('lot_id', '=', lot.id))
                    elif 'bl_id' in Container._fields:
                        domain.append(('bl_id', 'in', lot.bl_ids.ids))
                        
                    if len(domain) > 1:
                        released_count = Container.search_count(domain)
                        if released_count > 0:
                            # REFACTORED: Exception wrapped in _()
                            raise ValidationError(_(
                                "Validation Error: You cannot modify the Origin of this Lot because "
                                "one or more associated containers are currently in 'Released' status."
                            ))
                            
        return super().write(vals)

class OgiTransitBL(models.Model):
    _name = 'ogi.transit.bl'
    _description = 'Bill of Lading'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='B/L No.', required=True, tracking=True, copy=False)
    shipping_company_id = fields.Many2one('ogi.transit.shipping.company', string='Shipping Company')
    port_departure_id = fields.Many2one('ogi.transit.port', string='Port of Departure', required=True)
    port_arrival_id = fields.Many2one('ogi.transit.port', string='Port of Arrival', required=True)
    
    departure_date = fields.Date(string='Departure Date', required=True)
    expected_arrival_date = fields.Date(string='Expected Arrival Date', required=True)
    actual_arrival_date = fields.Date(string='Actual Arrival Date')
    
    lot_id = fields.Many2one('ogi.transit.lot', string='Parent Lot', required=True, ondelete='restrict')
    lot_origin = fields.Selection(related='lot_id.origin', string="Lot Origin", readonly=True)
    container_ids = fields.One2many('ogi.transit.container', 'bl_id', string='Containers')

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Validation Error: The Bill of Lading Number must be unique!')
    ]

    @api.constrains('name')
    def _check_unique_bl_name(self):
        for bl in self:
            if bl.name:
                duplicate = self.search([
                    ('name', '=ilike', bl.name), 
                    ('id', '!=', bl.id)
                ], limit=1)
                
                if duplicate:
                    # REFACTORED: Exception wrapped in _()
                    raise ValidationError(_("Validation Error: The Bill of Lading Number must be unique! A B/L with this number already exists in the system."))


class OgiTransitContainer(models.Model):
    _name = 'ogi.transit.container'
    _description = 'Container'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Container Number', index=True, required=True, tracking=True, copy=False)
    container_label = fields.Char(string='Container Label', tracking=True, help="Free text label (e.g. 2026)")

    type = fields.Selection([
        ('fcl_awaye', 'FCL + Away'),
        ('fcl_home', 'FCL + Home'),
        ('lcl_home', 'LCL + Home')
    ], string='Container Type', required=True, tracking=True)
    
    state = fields.Selection([
        ('prep', 'In preparation'),
        ('created', 'Created'),
        ('arrived', 'Arrived'),
        ('ready', 'Ready for release'),
        ('released', 'Released'),
        ('closed', 'Closed (Locked)')
    ], string='Status', default='created', tracking=True)

    packing_list_state = fields.Selection([
        ('draft', 'Draft'),
        ('validated', 'Validated')
    ], string='Packing List Status', default='draft', tracking=True)

    container_size = fields.Selection([
        ('20', '20"'),
        ('40', '40"')
    ], string='Container Size', required=True, tracking=True)
    
    goods_description = fields.Char(string='Description of Goods', tracking=True)
    
    bl_id = fields.Many2one('ogi.transit.bl', string='Bill of Lading', required=True, ondelete='restrict')
    origin = fields.Selection(related='bl_id.lot_id.origin', string='Origin', readonly=True)

    forwarder_id = fields.Many2one(
        'res.partner', 
        string='Freight Forwarder', 
        domain="[('contact_type', '=', 'freight_forwarder')]", 
        tracking=True
    )

    partner_id = fields.Many2one('res.partner', string='Customer (FCL)', tracking=True)
    usd_invoice_id = fields.Many2one('ogi.transit.invoice', string='FCL USD Invoice', readonly=True, copy=False)
    
    gnf_invoice_id = fields.Many2one('ogi.transit.invoice', string='FCL GNF Invoice', readonly=True, copy=False)

    delivery_note_id = fields.Many2one('ogi.transit.delivery.note', string='FCL Delivery Note', readonly=True, copy=False)

    # NEW: Invoice Summary Fields
    remaining_amount_usd = fields.Float(string='Remaining Amount (USD)', compute='_compute_invoice_summaries')
    remaining_amount_gnf = fields.Float(string='Remaining Amount (GNF)', compute='_compute_invoice_summaries')
    unpaid_usd_invoices_count = fields.Integer(string='Unpaid USD Invoices', compute='_compute_invoice_summaries')
    unpaid_gnf_invoices_count = fields.Integer(string='Unpaid GNF Invoices', compute='_compute_invoice_summaries')

    has_pl_lines = fields.Boolean(compute='_compute_has_pl_lines')

    total_freight_usd = fields.Float(string='Total Freight (USD)', tracking=True)
    total_ins_usd = fields.Float(string='Total INS (USD)', tracking=True) # NEW FIELD
    total_customs_gnf = fields.Float(string='Container Service Price (GNF)', tracking=True)
    # NEW: BGDA Amount for FCL + Home
    bgda = fields.Float(string='BGDA (GNF)', tracking=True)
    total_freight_forwarder_gnf = fields.Float(string='Freight Forwarder Cost (GNF)', tracking=True)
    estimated_gross_margin_gnf = fields.Float(string='Estimated Gross Margin (GNF)', compute='_compute_gross_margins', store=True, tracking=True)
    actual_gross_margin_gnf = fields.Float(string='Actual Gross Margin (GNF)', compute='_compute_gross_margins', store=True, tracking=True)
    
    total_cbm = fields.Float(string='Total CBM/Line', compute='_compute_total_cbm', store=True)
    total_ins_cbm = fields.Float(string='Total INS CBM', compute='_compute_total_ins_cbm', store=True) # NEW FIELD

    pl_line_ids = fields.One2many('ogi.transit.pl.line', 'container_id', string='Packing List Lines')

    @api.constrains('name')
    def check_container_name(self):
        for record in self:
            if record.name:
                # 1. Existing ISO format validation
                if not re.match(r'^[A-Z]{4}\d{7}$', record.name):
                    raise ValidationError(_("Invalid Container Number. The ISO format must be exactly 4 uppercase letters followed by 7 digits (e.g., MAEU1234567)."))
                
                # 2. Uniqueness validation (Case-insensitive check)
                duplicate = self.search([
                    ('name', '=ilike', record.name),
                    ('id', '!=', record.id)
                ], limit=1)
                
                if duplicate:
                    raise ValidationError(_("Container Number already exists. Please enter a unique container number."))

    # NEW: Positive value validation for Container financial fields
    @api.constrains('total_freight_forwarder_gnf', 'total_ins_usd')
    def _check_positive_container_financials(self):
        for container in self:
            if container.total_freight_forwarder_gnf < 0:
                raise ValidationError(_("Validation Error: Freight Forwarder Cost (GNF) cannot be a negative value."))
            if container.total_ins_usd < 0:
                raise ValidationError(_("Validation Error: Total INS (USD) cannot be a negative value."))

                
    @api.constrains('type', 'partner_id', 'total_freight_usd', 'total_customs_gnf', 'goods_description')
    def _check_fcl_required_fields(self):
        for container in self:
            if container.type in ['fcl_awaye', 'fcl_home']:
                if not container.partner_id:
                    raise ValidationError(_("Validation Error: The 'Customer' field is mandatory for FCL containers."))
                if not container.goods_description:
                    raise ValidationError(_("Validation Error: The 'Description of Goods' field is mandatory for FCL containers."))
                if container.total_freight_usd <= 0:
                    raise ValidationError(_("Validation Error: The 'Total Freight (USD)' must be strictly greater than zero."))
            if container.type == 'fcl_home':
                if container.total_customs_gnf <= 0:
                    raise ValidationError(_("Validation Error: The 'Container Service Price (GNF)' must be strictly greater than zero for FCL + Home containers."))

    @api.depends('pl_line_ids')
    def _compute_has_pl_lines(self):
        for container in self:
            container.has_pl_lines = bool(container.pl_line_ids)

    @api.depends(
        'type',
        'usd_invoice_id.amount_residual', 'usd_invoice_id.state',
        'gnf_invoice_id.amount_residual', 'gnf_invoice_id.state',
        'pl_line_ids.usd_invoice_id.amount_residual', 'pl_line_ids.usd_invoice_id.state',
        'pl_line_ids.gnf_invoice_id.amount_residual', 'pl_line_ids.gnf_invoice_id.state'
    )
    def _compute_invoice_summaries(self):
        for container in self:
            rem_usd = 0.0
            rem_gnf = 0.0
            unpaid_usd_count = 0
            unpaid_gnf_count = 0
            
            # FCL Calculations
            if container.type in ['fcl_awaye', 'fcl_home']:
                # ADDED 'draft' to the accepted states
                if container.usd_invoice_id and container.usd_invoice_id.state in ['draft', 'issued', 'partial']:
                    rem_usd += container.usd_invoice_id.amount_residual
                    
                # ADDED 'draft' to the accepted states
                if container.type == 'fcl_home' and container.gnf_invoice_id and container.gnf_invoice_id.state in ['draft', 'issued', 'partial']:
                    rem_gnf += container.gnf_invoice_id.amount_residual
                    
            # LCL Calculations
            elif container.type == 'lcl_home':
                for line in container.pl_line_ids:
                    # ADDED 'draft' to the accepted states
                    if line.usd_invoice_id and line.usd_invoice_id.state in ['draft', 'issued', 'partial']:
                        rem_usd += line.usd_invoice_id.amount_residual
                        unpaid_usd_count += 1
                        
                    # ADDED 'draft' to the accepted states
                    if line.gnf_invoice_id and line.gnf_invoice_id.state in ['draft', 'issued', 'partial']:
                        rem_gnf += line.gnf_invoice_id.amount_residual
                        unpaid_gnf_count += 1
                        
            container.remaining_amount_usd = rem_usd
            container.remaining_amount_gnf = rem_gnf
            container.unpaid_usd_invoices_count = unpaid_usd_count
            container.unpaid_gnf_invoices_count = unpaid_gnf_count

    @api.depends('pl_line_ids.cbm_line')
    def _compute_total_cbm(self):
        for container in self:
            container.total_cbm = sum(container.pl_line_ids.mapped('cbm_line'))

    @api.depends('pl_line_ids.ins_cbm')
    def _compute_total_ins_cbm(self):
        for container in self:
            container.total_ins_cbm = sum(container.pl_line_ids.mapped('ins_cbm'))

    @api.depends('total_customs_gnf', 'total_freight_forwarder_gnf', 'pl_line_ids.calculated_gnf')
    def _compute_gross_margins(self):
        for container in self:
            # 1. Estimated Gross Margin (Target Revenue - Cost)
            container.estimated_gross_margin_gnf = container.total_customs_gnf - container.total_freight_forwarder_gnf
            
            # 2. Actual Gross Margin (Actual Invoiced Revenue - Cost)
            total_prorata_customs = sum(container.pl_line_ids.mapped('calculated_gnf'))
            
            # BUG FIX: Subtract Freight Forwarder Cost from the Total Prorated amount, not the Service Price
            if total_prorata_customs > 0:
                container.actual_gross_margin_gnf = total_prorata_customs - container.total_freight_forwarder_gnf
            else:
                # If prorata hasn't been calculated yet, fall back to 0.0 to prevent confusion
                container.actual_gross_margin_gnf = 0.0

    @api.constrains('type', 'forwarder_id')
    def _check_mandatory_forwarder(self):
        for container in self:
            if container.type in ['fcl_home', 'lcl_home'] and not container.forwarder_id:
                raise ValidationError(_("Validation Error: A Freight Forwarder must be assigned for 'FCL + Home' and 'LCL + Home' containers."))

    @api.constrains('type', 'total_customs_gnf', 'total_freight_forwarder_gnf')
    def _check_margin_validity(self):
        for container in self:
            if container.type in ['fcl_home', 'lcl_home']:
                if container.total_customs_gnf > 0 and container.total_freight_forwarder_gnf > 0:
                    if container.total_customs_gnf <= container.total_freight_forwarder_gnf:
                        raise ValidationError(_("Business Rule Error: The 'Container Service Price (GNF)' must be strictly greater than the 'Freight Forwarder Cost (GNF)'."))


    def action_calculate_prorata(self):
        for container in self:
            if container.total_cbm <= 0:
                raise ValidationError(_("Total CBM/Line must be greater than zero to calculate prorated amounts."))
            
            for line in container.pl_line_ids:
                # 1. Base Freight USD
                base_usd = (container.total_freight_usd / container.total_cbm) * line.cbm_line
                
                # 2. Base Customs GNF (UPDATED FORMULA: Uses Container Service Price)
                base_gnf = (container.total_customs_gnf / container.total_cbm) * line.cbm_line
                
                # 3. Prorata INS Fee
                if container.total_ins_cbm > 0:
                    ins_usd = (line.ins_cbm / container.total_ins_cbm) * container.total_ins_usd
                else:
                    ins_usd = 0.0

                # 4. Rounding logic for USD
                from odoo.tools import float_round
                base_usd_rounded = float_round(base_usd, precision_digits=0, rounding_method='HALF-UP')
                ins_usd_rounded = float_round(ins_usd, precision_digits=0, rounding_method='HALF-UP')
                
                # Final USD Calculations
                raw_usd = base_usd_rounded + ins_usd_rounded
                
                line.prorata_freight_usd = base_usd_rounded
                line.calculated_ins_usd = ins_usd_rounded
                line.calculated_usd = raw_usd
                
                # 5. UPDATED GNF LOGIC & STRICT 5000 ROUNDING
                rounded_customs_gnf = math.ceil(base_gnf / 5000.0) * 5000
                
                line.calculated_gnf = rounded_customs_gnf
                line.total_gnf = rounded_customs_gnf + line.bgda

    def action_validate_packing_list(self):
        for container in self:
            if not container.pl_line_ids:
                raise ValidationError(_("You must add at least one Packing List line before validating."))
            
            # Strict LCL + Home Validation Rules
            if container.type == 'lcl_home':
                # 1. Origin & CBM Checks
                if container.origin == 'china' and round(container.total_cbm, 2) != 68.0:
                    raise ValidationError(_("Validation Error: For China origin containers, the Total CBM/Line must be exactly 68.0."))
                
                if container.origin == 'dubai' and round(container.total_cbm, 2) != 43.0:
                    raise ValidationError(_("Validation Error: For Dubai origin containers, the Total CBM/Line must be exactly 43.0."))
                
                # 2. Financial Checks
                if container.total_freight_usd <= 0:
                    raise ValidationError(_("Validation Error: Total Freight (USD) must be strictly greater than 0."))
                
                if container.total_customs_gnf <= 0:
                    raise ValidationError(_("Validation Error: Container Service Price (GNF) must be strictly greater than 0."))
                
                if container.total_freight_forwarder_gnf <= 0:
                    raise ValidationError(_("Validation Error: Freight Forwarder Cost (GNF) must be strictly greater than 0."))

                # 3. NEW: INS Validation Check
                if container.origin != 'dubai':
                    if container.total_ins_cbm > 0 and container.total_ins_usd <= 0:
                        raise ValidationError(_("Validation Error: You have entered INS CBM values in the Packing List. Therefore, the 'Total INS (USD)' field is mandatory and must be strictly greater than 0 to calculate the prorata."))

            container.packing_list_state = 'validated'
            container.message_post(body=Markup(_("<strong>Packing List Validated:</strong> Input data is confirmed.")))

    def action_generate_invoices(self):
        for container in self:
            Invoice = self.env['ogi.transit.invoice']
            
            # FCL AWAYE LOGIC
            if container.type == 'fcl_awaye':
                if not container.partner_id or container.total_freight_usd <= 0:
                    raise ValidationError(_("Customer and Total Freight USD are required to generate an FCL Away invoice."))
                
                if container.usd_invoice_id:
                    raise ValidationError(_("An invoice has already been generated for this container."))
                    
                inv_usd = Invoice.create({
                    'container_id': container.id,
                    'partner_id': container.partner_id.id,
                    'invoice_type': 'fcl_usd',
                    'currency': 'USD',
                    # UPDATED: Sum Freight and INS for the final invoice total
                    'amount_total': container.total_freight_usd + container.total_ins_usd,
                    'goods_description': container.goods_description,
                    # NEW: Explicitly track the INS amount on the invoice
                    'ins_amount': container.total_ins_usd,
                    'state': 'draft'
                })
                container.usd_invoice_id = inv_usd.id
                container.message_post(body=Markup(_("<strong>Success:</strong> 1 DRAFT USD invoice was generated for this FCL Away container.")))

            # FCL HOME LOGIC
            elif container.type == 'fcl_home':
                if not container.forwarder_id:
                    raise ValidationError(_("Validation Error: A Freight Forwarder (Transitaire) MUST be assigned before generating invoices."))
                
                invoices_created = 0
                if not container.usd_invoice_id:
                    inv_usd = Invoice.create({
                        'container_id': container.id,
                        'partner_id': container.partner_id.id,
                        'invoice_type': 'fcl_usd',
                        'currency': 'USD',
                        # UPDATED: Sum Freight and INS for the final invoice total
                        'amount_total': container.total_freight_usd + container.total_ins_usd,
                        'goods_description': container.goods_description,
                        # NEW: Explicitly track the INS amount on the invoice
                        'ins_amount': container.total_ins_usd,
                        'state': 'draft'
                    })
                    container.usd_invoice_id = inv_usd.id
                    invoices_created += 1

                if not container.gnf_invoice_id:
                    inv_gnf = Invoice.create({
                        'container_id': container.id,
                        'partner_id': container.partner_id.id,
                        'invoice_type': 'fcl_gnf',
                        'currency': 'GNF',
                        # UPDATED: Sum the Container Service Price and BGDA for the final invoice total
                        'amount_total': container.total_customs_gnf + container.bgda,
                        'goods_description': container.goods_description,
                        # NEW: Explicitly track the BGDA amount on the invoice line
                        'bgda_amount': container.bgda,
                        'state': 'draft'
                    })
                    container.gnf_invoice_id = inv_gnf.id
                    invoices_created += 1

                if invoices_created > 0:
                    container.message_post(body=Markup(_("<strong>Success:</strong> %s DRAFT invoice(s) generated for this FCL Home container.")) % invoices_created)
                
            # LCL HOME LOGIC
            elif container.type == 'lcl_home':
                # Check total_gnf instead of calculated_gnf
                if not container.pl_line_ids or any(line.calculated_usd == 0 and line.total_gnf == 0 for line in container.pl_line_ids):
                    raise ValidationError(_("Please run 'Calculate Pro-rata' to preview the amounts before generating invoices."))
                
                if not container.forwarder_id:
                    raise ValidationError(_(
                        "Validation Error: A Freight Forwarder (Transitaire) MUST be assigned to this container before generating invoices. "
                        "Please use the 'Bulk Assign' action or edit the container to assign one."
                    ))
                
                invoices_created = 0
                for line in container.pl_line_ids:
                    # 1. USD Invoice Creation
                    if line.calculated_usd > 0 and not line.usd_invoice_id:
                        inv_usd = Invoice.create({
                            'container_id': container.id,
                            'partner_id': line.partner_id.id,
                            'invoice_type': 'lcl_usd',
                            'currency': 'USD',
                            'amount_total': line.calculated_usd,
                            'goods_description': line.goods_description,
                            
                            # NEW: Explicitly track the prorated INS amount on the LCL invoice
                            'ins_amount': line.calculated_ins_usd,
                            
                            'state': 'draft'
                        })
                        line.usd_invoice_id = inv_usd.id
                        invoices_created += 1
                    
                    # 2. GNF Invoice Creation (Merged and Corrected)
                    if line.total_gnf > 0 and not line.gnf_invoice_id:
                        inv_gnf = Invoice.create({
                            'container_id': container.id,
                            'partner_id': line.partner_id.id,
                            'invoice_type': 'lcl_gnf',
                            'currency': 'GNF',
                            'amount_total': line.total_gnf,      # Correct Total Amount
                            'goods_description': line.goods_description,
                            'bgda_amount': line.bgda,            # Preserved BGDA Tracking
                            'state': 'draft'
                        })
                        line.gnf_invoice_id = inv_gnf.id         # Correct ID Assignment
                        invoices_created += 1
                
                log_message = Markup(_("<strong>Success:</strong> %s DRAFT invoices were generated for this container.")) % invoices_created
                container.message_post(body=log_message)

    can_issue_delivery_notes = fields.Boolean(compute='_compute_can_issue_delivery_notes')

    @api.depends('type', 'pl_line_ids.calculated_usd', 'pl_line_ids.usd_invoice_id.state', 'pl_line_ids.gnf_invoice_id.state', 'usd_invoice_id.state', 'gnf_invoice_id.state')
    def _compute_can_issue_delivery_notes(self):
        for container in self:
            if container.type == 'lcl_home' and container.pl_line_ids:
                if any(line.calculated_usd == 0 for line in container.pl_line_ids):
                    container.can_issue_delivery_notes = False
                    continue
                all_issued = True
                for line in container.pl_line_ids:
                    if not line.usd_invoice_id or not line.gnf_invoice_id or line.usd_invoice_id.state == 'draft' or line.gnf_invoice_id.state == 'draft':
                        all_issued = False
                        break
                container.can_issue_delivery_notes = all_issued
            elif container.type == 'fcl_home':
                if container.usd_invoice_id and container.gnf_invoice_id and \
                   container.usd_invoice_id.state != 'draft' and container.gnf_invoice_id.state != 'draft' and \
                   not container.delivery_note_id:
                    container.can_issue_delivery_notes = True
                else:
                    container.can_issue_delivery_notes = False
            else:
                container.can_issue_delivery_notes = False

    def action_issue_delivery_notes(self):
        for container in self:
            DeliveryNote = self.env['ogi.transit.delivery.note']
            
            # FCL HOME Delivery Note
            if container.type == 'fcl_home':
                if not container.usd_invoice_id or container.usd_invoice_id.state == 'draft' or \
                   not container.gnf_invoice_id or container.gnf_invoice_id.state == 'draft':
                    # REFACTORED: Exception wrapped in _()
                    raise ValidationError(_("Cannot issue delivery notes. Both USD and GNF invoices must be issued first."))
                
                if not container.delivery_note_id:
                    note = DeliveryNote.create({
                        'container_id': container.id,
                        'partner_id': container.partner_id.id,
                        # REFACTORED: Converted f-string to %s formatting and wrapped in _()
                        'operator_note': _("FCL Home Delivery for %s") % container.name
                    })
                    container.delivery_note_id = note.id
                    # REFACTORED: String wrapped in _()
                    container.message_post(body=Markup(_("<strong>Generated:</strong> 1 Delivery Note.")))

            # LCL HOME Delivery Notes
            elif container.type == 'lcl_home':
                if not container.pl_line_ids or any(l.calculated_usd == 0 for l in container.pl_line_ids):
                    # REFACTORED: Exception wrapped in _()
                    raise ValidationError(_("You must calculate the pro-rata amounts before issuing Delivery Notes."))
                
                for line in container.pl_line_ids:
                    if (line.usd_invoice_id and line.usd_invoice_id.state == 'draft') or \
                       (line.gnf_invoice_id and line.gnf_invoice_id.state == 'draft'):
                        # REFACTORED: Converted f-string to %s formatting and wrapped in _()
                        raise ValidationError(_("Cannot issue delivery notes. The invoice for %s is still in Draft. Please Issue all invoices first.") % line.partner_id.name)

                notes_created = 0
                for line in container.pl_line_ids:
                    if not line.delivery_note_id:
                        note = DeliveryNote.create({
                            'pl_line_id': line.id,
                            'container_id': container.id,
                            'partner_id': line.partner_id.id
                        })
                        line.delivery_note_id = note.id
                        notes_created += 1
                
                # REFACTORED: Converted f-string to %s formatting and wrapped in _()
                container.message_post(body=Markup(_("<strong>Generated:</strong> %s Delivery Notes.")) % notes_created)
            else:
                # REFACTORED: Exception wrapped in _()
                raise ValidationError(_("Delivery notes are only issued for FCL Home and LCL Home containers."))

    def _validate_closure_rules(self):
        DeliveryNote = self.env['ogi.transit.delivery.note']
        Invoice = self.env['ogi.transit.invoice']
        
        for container in self:
            # 1. Invoice Checks (Applies universally to ALL container types)
            invoices = Invoice.search([
                ('container_id', '=', container.id),
                ('state', '!=', 'canceled') # Canceled invoices do not count towards the requirement
            ])
            
            if not invoices:
                raise ValidationError(_("Cannot lock container: No invoices have been generated. At least one invoice must exist."))
                
            unpaid_invoices = invoices.filtered(lambda inv: inv.state != 'paid')
            if unpaid_invoices:
                raise ValidationError(_("Cannot lock container: All related invoices must be fully settled/paid before closing."))
                
            # 2. Delivery Checks (Applies to FCL+Home and LCL+Home)
            if container.type in ['fcl_home', 'lcl_home']:
                deliveries = DeliveryNote.search([('container_id', '=', container.id)])
                
                if not deliveries:
                    raise ValidationError(_("Cannot lock container: No delivery notes have been issued. All goods must be delivered first."))
                    
                pending_deliveries = deliveries.filtered(lambda d: d.logistics_status != 'retrieved')
                if pending_deliveries:
                    raise ValidationError(_("Cannot lock container: Not all goods have been fully delivered. All delivery notes must be in 'Retrieved' status."))
                
    def action_lock_container(self):
        self._validate_closure_rules()
        for container in self:
            container.state = 'closed'
            container.message_post(body=Markup(_("<strong>File Closed:</strong> Container locked by Manager. All balances settled and all goods delivered.")))

    def write(self, vals):
        if vals.get('state') == 'closed':
            self._validate_closure_rules()

        if 'state' in vals:
            state_order = {
                'prep': 0, 'created': 1, 'arrived': 2, 'ready': 3, 'released': 4, 'closed': 5
            }
            new_state_index = state_order.get(vals['state'], -1)
            
            for container in self:
                old_state_index = state_order.get(container.state, -1)
                if old_state_index > -1 and new_state_index > -1 and new_state_index < old_state_index:
                    is_manager = self.env.user.has_group('ogi_transit.group_ogi_gerant')
                    is_ceo = self.env.user.has_group('ogi_transit.group_ogi_pdg')
                    is_admin = self.env.user.has_group('ogi_transit.group_ogi_admin')
                    
                    if not (is_manager or is_ceo or is_admin):
                        raise ValidationError(_("Security Restriction: Only a Manager, CEO, or Admin can roll back a container to a previous status."))
                        
        res = super(OgiTransitContainer, self).write(vals)
        
        if vals.get('state') == 'released':
            for container in self:
                notes = self.env['ogi.transit.delivery.note'].search([
                    ('container_id', '=', container.id),
                    ('logistics_status', '=', 'pending')
                ])
                if notes:
                    notes.write({'logistics_status': 'unpacked'})
                    container.message_post(body=Markup(_("<strong>Automation:</strong> %s Delivery Note(s) automatically updated to 'Depoting' (Unpacked).")) % len(notes))

        # ==========================================
        # NEW: Intercept 'arrived' status
        # ==========================================
        if vals.get('state') == 'arrived':
            for container in self:
                if container.type in ('fcl_home', 'lcl_home'):
                    container._generate_freight_forwarder_bill()

        return res

    def _generate_freight_forwarder_bill(self):
        VendorBill = self.env['ogi.transit.vendor.bill']
        
        for container in self:
            if not container.forwarder_id:
                continue

            bills_created = 0

            # 1. Generate Freight Forwarder Cost (Customs Clearance) Bill
            if container.total_freight_forwarder_gnf > 0:
                existing_customs = VendorBill.search([
                    ('container_id', '=', container.id),
                    ('expense_type', '=', 'customs')
                ], limit=1)
                
                if not existing_customs:
                    VendorBill.create({
                        'partner_id': container.forwarder_id.id,
                        'container_id': container.id,
                        'expense_type': 'customs',
                        'currency': 'GNF',
                        'amount_total': container.total_freight_forwarder_gnf,
                        'description': _('Automated Freight Forwarder Cost for %s') % container.name,
                        'state': 'draft'
                    })
                    bills_created += 1

            # 2. Generate BGDA Bill
            total_bgda = 0.0
            if container.type == 'fcl_home':
                total_bgda = container.bgda
            elif container.type == 'lcl_home':
                total_bgda = sum(container.pl_line_ids.mapped('bgda'))

            if total_bgda > 0:
                existing_bgda = VendorBill.search([
                    ('container_id', '=', container.id),
                    ('expense_type', '=', 'bgda')
                ], limit=1)
                
                if not existing_bgda:
                    VendorBill.create({
                        'partner_id': container.forwarder_id.id,
                        'container_id': container.id,
                        'expense_type': 'bgda',
                        'currency': 'GNF',
                        'amount_total': total_bgda,
                        'description': _('Automated BGDA Cost for %s') % container.name,
                        'state': 'draft'
                    })
                    bills_created += 1

            if bills_created > 0:
                container.message_post(body=Markup(_("<strong>Automation:</strong> %s Draft Vendor Bill(s) generated for Freight Forwarder.")) % bills_created)


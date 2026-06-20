import re
from odoo import models, fields, api
from odoo.exceptions import ValidationError
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
        ('dubai', 'Dubai')
    ], string='Origin Country', required=True)

# ==========================================
# NEW DELIVERY NOTE MODEL
# ==========================================
class OgiTransitDeliveryNote(models.Model):
    _name = 'ogi.transit.delivery.note'
    _description = 'Delivery Note'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Reference', required=True, copy=False, default='Draft', tracking=True)
    pl_line_id = fields.Many2one('ogi.transit.pl.line', string='Packing List Line', required=True, ondelete='cascade')
    container_id = fields.Many2one(related='pl_line_id.container_id', store=True, string='Container')
    partner_id = fields.Many2one(related='pl_line_id.partner_id', store=True, string='Customer')
    
    logistics_status = fields.Selection([
        ('pending', 'Pending at Port'),
        ('unpacked', 'Unpacked (Depoting)'),
        ('storage', 'In Storage'),
        ('retrieved', 'Retrieved by Customer')
    ], string='Logistics Status', default='pending', tracking=True)
    
    operator_note = fields.Text(string='Delivery Notes / Comments', tracking=True)

    # NEW: Standard Odoo sequence generator override
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals.get('name') in ('Draft', 'New Note'):
                vals['name'] = self.env['ir.sequence'].next_by_code('ogi.transit.delivery.note') or 'BL-ERROR'
        return super(OgiTransitDeliveryNote, self).create(vals_list)

# ==========================================
# INHERITS FOR EXISTING MODELS
# ==========================================
class OgiTransitPlLine(models.Model):
    _inherit = 'ogi.transit.pl.line'
    
    # NEW: Dynamic name for clean display in dropdowns and related fields
    name = fields.Char(string='Line Reference', compute='_compute_name', store=True)
    delivery_note_id = fields.Many2one('ogi.transit.delivery.note', string='Delivery Note', readonly=True)

    @api.depends('partner_id.name', 'container_id.name')
    def _compute_name(self):
        for line in self:
            if line.partner_id and line.container_id:
                line.name = f"{line.partner_id.name} - {line.container_id.name}"
            else:
                line.name = "New Line"

class OgiTransitInvoice(models.Model):
    _inherit = 'ogi.transit.invoice'

    goods_description = fields.Char(string='Description of Goods')
    bgda_amount = fields.Float(string='BGDA Amount')


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

    # Deep database constraint for unique names
    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Validation Error: The Lot Number must be unique!')
    ]

    # Python constraint to catch duplicates instantly
    @api.constrains('name')
    def _check_unique_lot_name(self):
        for lot in self:
            if lot.name:
                duplicate = self.search([
                    ('name', '=ilike', lot.name), 
                    ('id', '!=', lot.id)
                ], limit=1)
                if duplicate:
                    raise ValidationError("Validation Error: The Lot Number must be unique! A lot with this number already exists in the system.")

    # NEW: Security intercept to block Origin modification if Containers are Released
    def write(self, vals):
        # Only run this check if the user is actually trying to edit the 'origin'
        if 'origin' in vals:
            for lot in self:
                # Only trigger if the origin is genuinely changing from its current value
                if lot.origin and lot.origin != vals['origin']:
                    Container = self.env['ogi.transit.container'].sudo()
                    domain = [('state', '=', 'released')]
                    
                    # We dynamically check how your Containers link to Lots to ensure this doesn't crash.
                    # It checks if the link is direct (lot_id) or via the Bill of Lading (bl_id)
                    if 'lot_id' in Container._fields:
                        domain.append(('lot_id', '=', lot.id))
                    elif 'bl_id' in Container._fields:
                        domain.append(('bl_id', 'in', lot.bl_ids.ids))
                        
                    # If we successfully established the link condition, execute the search
                    if len(domain) > 1:
                        released_count = Container.search_count(domain)
                        if released_count > 0:
                            raise ValidationError(
                                "Validation Error: You cannot modify the Origin of this Lot because "
                                "one or more associated containers are currently in 'Released' status."
                            )
                            
        return super().write(vals)

class OgiTransitBL(models.Model):
    _name = 'ogi.transit.bl'
    _description = 'Bill of Lading'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='B/L No.', required=True, tracking=True, copy=False)
    
    # FIX 1: Removed required=True to prevent PostgreSQL NOT NULL crashes on existing records
    shipping_company_id = fields.Many2one('ogi.transit.shipping.company', string='Shipping Company')
    port_departure_id = fields.Many2one('ogi.transit.port', string='Port of Departure')
    
    port_arrival = fields.Char(string='Port of Arrival', required=True)
    departure_date = fields.Date(string='Departure Date', required=True)
    expected_arrival_date = fields.Date(string='Expected Arrival Date', required=True)
    actual_arrival_date = fields.Date(string='Actual Arrival Date')
    
    lot_id = fields.Many2one('ogi.transit.lot', string='Parent Lot', required=True, ondelete='restrict')
    
    # FIX 2: Removed store=True so the UI updates instantly when Lot is selected
    lot_origin = fields.Selection(related='lot_id.origin', string="Lot Origin", readonly=True)
    container_ids = fields.One2many('ogi.transit.container', 'bl_id', string='Containers')

    # NEW: SQL Constraint for deep database security
    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Validation Error: The Bill of Lading Number must be unique!')
    ]

    # NEW: Python-level constraint to guarantee the UI catches the duplicate instantly (case-insensitive)
    @api.constrains('name')
    def _check_unique_bl_name(self):
        for bl in self:
            if bl.name:
                duplicate = self.search([
                    ('name', '=ilike', bl.name), 
                    ('id', '!=', bl.id)
                ], limit=1)
                
                if duplicate:
                    raise ValidationError("Validation Error: The Bill of Lading Number must be unique! A B/L with this number already exists in the system.")

class OgiTransitContainer(models.Model):
    _name = 'ogi.transit.container'
    _description = 'Container'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    name = fields.Char(string='Container Number', index=True, required=True, tracking=True, copy=False)
    container_label = fields.Char(string='Container Label', tracking=True, help="Free text label (e.g. 2026)")

    type = fields.Selection([
        ('fcl_awaye', 'FCL + Awaye'),
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
    
    bl_id = fields.Many2one('ogi.transit.bl', string='Bill of Lading', required=True, ondelete='restrict')
    origin = fields.Selection(related='bl_id.lot_id.origin', string='Origin', readonly=True)

    # NEW: Freight Forwarder Assignment
    forwarder_id = fields.Many2one(
        'res.partner', 
        string='Freight Forwarder', 
        domain="[('contact_type', '=', 'freight_forwarder')]", 
        tracking=True
    )

    # NEW: FCL Awaye Direct Billing Fields
    partner_id = fields.Many2one('res.partner', string='Customer (FCL Awaye)', tracking=True)
    usd_invoice_id = fields.Many2one('ogi.transit.invoice', string='FCL Awaye Invoice', readonly=True, copy=False)

    has_pl_lines = fields.Boolean(compute='_compute_has_pl_lines')

    total_freight_usd = fields.Float(string='Total Freight (USD)', tracking=True)
    total_customs_gnf = fields.Float(string='Container Service Price (GNF)', tracking=True)
    total_freight_forwarder_gnf = fields.Float(string='Freight Forwarder Cost (GNF)', tracking=True)
    total_cbm = fields.Float(string='Total CBM/Line', compute='_compute_total_cbm', store=True)

    pl_line_ids = fields.One2many('ogi.transit.pl.line', 'container_id', string='Packing List Lines')

    # STRICT ISO FORMAT VALIDATION
    @api.constrains('name')
    def _check_container_name(self):
        for record in self:
            if record.name and not re.match(r'^[A-Z]{4}\d{7}$', record.name):
                raise ValidationError("Invalid Container Number. The ISO format must be exactly 4 uppercase letters followed by 7 digits (e.g., MAEU1234567).")

    # NEW: Validate Mandatory Fields for FCL Awaye
    @api.constrains('type', 'partner_id', 'total_freight_usd')
    def _check_fcl_awaye_required_fields(self):
        for container in self:
            if container.type == 'fcl_awaye':
                if not container.partner_id:
                    raise ValidationError("Validation Error: The 'Customer' field is mandatory for FCL + AWAYE containers.")
                if container.total_freight_usd <= 0:
                    raise ValidationError("Validation Error: The 'Total Freight (USD)' must be strictly greater than zero for FCL + AWAYE containers.")

    @api.depends('pl_line_ids')
    def _compute_has_pl_lines(self):
        for container in self:
            container.has_pl_lines = bool(container.pl_line_ids)

    @api.depends('pl_line_ids.cbm_line')
    def _compute_total_cbm(self):
        for container in self:
            container.total_cbm = sum(container.pl_line_ids.mapped('cbm_line'))

    def action_calculate_prorata(self):
        for container in self:
            if container.total_cbm <= 0:
                raise ValidationError("Total CBM/Line must be greater than zero to calculate prorated amounts.")
            
            for line in container.pl_line_ids:
                base_usd = (container.total_freight_usd / container.total_cbm) * line.cbm_line
                base_gnf = (container.total_customs_gnf / container.total_cbm) * line.cbm_line
                
                ins = line.ins_fee if container.origin == 'china' else 0.0
                raw_usd = base_usd + ins
                raw_gnf = base_gnf + line.bgda
                
                line.calculated_usd = round(raw_usd)
                line.calculated_gnf = round(raw_gnf / 5000.0) * 5000

    # UPDATED: Handles both FCL Awaye (Direct) and LCL Home (PL Lines)
    def action_generate_invoices(self):
        for container in self:
            Invoice = self.env['ogi.transit.invoice']
            
            # FCL AWAYE LOGIC
            if container.type == 'fcl_awaye':
                if not container.partner_id or container.total_freight_usd <= 0:
                    raise ValidationError("Customer and Total Freight USD are required to generate an FCL Awaye invoice.")
                
                if container.usd_invoice_id:
                    raise ValidationError("An invoice has already been generated for this container.")
                    
                inv_usd = Invoice.create({
                    'container_id': container.id,
                    'partner_id': container.partner_id.id,
                    'invoice_type': 'fcl_usd',
                    'currency': 'USD',
                    'amount_total': container.total_freight_usd,
                    'goods_description': f"FCL Awaye Freight - {container.name}",
                    'state': 'draft'
                })
                container.usd_invoice_id = inv_usd.id
                container.message_post(body=Markup("<strong>Success:</strong> 1 DRAFT USD invoice was generated for this FCL Awaye container."))
                
            # LCL HOME LOGIC
            elif container.type == 'lcl_home':
                if not container.pl_line_ids or any(line.calculated_usd == 0 and line.calculated_gnf == 0 for line in container.pl_line_ids):
                    raise ValidationError("Please run 'Calculate Pro-rata' to preview the amounts before generating invoices.")
                
                # NEW: Block invoice generation if no forwarder is assigned
                if not container.forwarder_id:
                    raise ValidationError(
                        "Validation Error: A Freight Forwarder (Transitaire) MUST be assigned to this container before generating invoices. "
                        "Please use the 'Bulk Assign' action or edit the container to assign one."
                    )
                
                invoices_created = 0
                for line in container.pl_line_ids:
                    if line.calculated_usd > 0 and not line.usd_invoice_id:
                        inv_usd = Invoice.create({
                            'container_id': container.id,
                            'partner_id': line.partner_id.id,
                            'invoice_type': 'lcl_usd',
                            'currency': 'USD',
                            'amount_total': line.calculated_usd,
                            'goods_description': line.goods_description,
                            'state': 'draft'
                        })
                        line.usd_invoice_id = inv_usd.id
                        invoices_created += 1
                    
                    if line.calculated_gnf > 0 and not line.gnf_invoice_id:
                        inv_gnf = Invoice.create({
                            'container_id': container.id,
                            'partner_id': line.partner_id.id,
                            'invoice_type': 'lcl_gnf',
                            'currency': 'GNF',
                            'amount_total': line.calculated_gnf,
                            'goods_description': line.goods_description,
                            'bgda_amount': line.bgda,
                            'state': 'draft'
                        })
                        line.gnf_invoice_id = inv_gnf.id
                        invoices_created += 1
                
                log_message = Markup("<strong>Success:</strong> %s DRAFT invoices were generated for this container.") % invoices_created
                container.message_post(body=log_message)

    def action_issue_delivery_notes(self):
        for container in self:
            if container.type != 'lcl_home':
                raise ValidationError("Delivery notes are only issued for LCL Home packing lists.")
                
            if not container.pl_line_ids or any(l.calculated_usd == 0 for l in container.pl_line_ids):
                raise ValidationError("You must calculate the pro-rata amounts before issuing Delivery Notes.")
            
            for line in container.pl_line_ids:
                if (line.usd_invoice_id and line.usd_invoice_id.state == 'draft') or \
                   (line.gnf_invoice_id and line.gnf_invoice_id.state == 'draft'):
                    raise ValidationError(f"Cannot issue delivery notes. The invoice for {line.partner_id.name} is still in Draft. Please Issue all invoices first.")

            DeliveryNote = self.env['ogi.transit.delivery.note']
            notes_created = 0
            for line in container.pl_line_ids:
                if not line.delivery_note_id:
                    note = DeliveryNote.create({
                        'pl_line_id': line.id
                    })
                    line.delivery_note_id = note.id
                    notes_created += 1
                    
            container.message_post(body=Markup(f"<strong>Generated:</strong> {notes_created} Delivery Notes."))
    
    can_issue_delivery_notes = fields.Boolean(compute='_compute_can_issue_delivery_notes')

    @api.depends('type', 'pl_line_ids.calculated_usd', 'pl_line_ids.usd_invoice_id.state', 'pl_line_ids.gnf_invoice_id.state')
    def _compute_can_issue_delivery_notes(self):
        for container in self:
            if container.type != 'lcl_home' or not container.pl_line_ids:
                container.can_issue_delivery_notes = False
                continue
                
            if any(line.calculated_usd == 0 for line in container.pl_line_ids):
                container.can_issue_delivery_notes = False
                continue
                
            all_issued = True
            for line in container.pl_line_ids:
                if not line.usd_invoice_id or not line.gnf_invoice_id:
                    all_issued = False
                    break
                if line.usd_invoice_id.state == 'draft' or line.gnf_invoice_id.state == 'draft':
                    all_issued = False
                    break
                    
            container.can_issue_delivery_notes = all_issued
    
    # UPDATED: Enforce lock logic cleanly for both types
    def action_lock_container(self):
        for container in self:
            if container.type == 'fcl_awaye':
                if container.usd_invoice_id and container.usd_invoice_id.state != 'paid':
                    raise ValidationError("Cannot lock container: The USD invoice is not paid.")
            else:
                unpaid_usd = container.pl_line_ids.mapped('usd_invoice_id').filtered(lambda i: i.state != 'paid')
                if unpaid_usd:
                    raise ValidationError("Cannot lock container: There are unpaid USD invoices.")
                
                unpaid_gnf = container.pl_line_ids.mapped('gnf_invoice_id').filtered(lambda i: i.state != 'paid')
                if unpaid_gnf:
                    raise ValidationError("Cannot lock container: There are unpaid GNF invoices.")
                
            container.state = 'closed'
            container.message_post(body="<strong>File Closed:</strong> Container locked by Manager. All balances settled.")

    # NEW: Security intercept to prevent unauthorized container status rollbacks
    def write(self, vals):
        if 'state' in vals:
            # 1. Define the strict logical order of operations
            state_order = {
                'prep': 0, 
                'created': 1, 
                'arrived': 2, 
                'ready': 3, 
                'released': 4, 
                'closed': 5
            }
            new_state_index = state_order.get(vals['state'], -1)
            
            for container in self:
                old_state_index = state_order.get(container.state, -1)
                
                # 2. If the new state index is lower than the old one, it is a rollback
                if old_state_index > -1 and new_state_index > -1 and new_state_index < old_state_index:
                    
                    # 3. Check if the current user has the authority to roll back
                    is_manager = self.env.user.has_group('ogi_transit.group_ogi_gerant')
                    is_ceo = self.env.user.has_group('ogi_transit.group_ogi_pdg')
                    is_admin = self.env.user.has_group('ogi_transit.group_ogi_admin')
                    
                    if not (is_manager or is_ceo or is_admin):
                        raise ValidationError(
                            "Security Restriction: Only a Manager, CEO, or Admin can roll back a container to a previous status."
                        )
                        
        return super(OgiTransitContainer, self).write(vals)
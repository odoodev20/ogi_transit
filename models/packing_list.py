from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class OgiTransitPLLine(models.Model):
    _name = 'ogi.transit.pl.line'
    _description = 'Packing List Line'
    _order = 'id asc'

    container_id = fields.Many2one('ogi.transit.container', string='Container', required=True, ondelete='cascade')
    
    # 1. CORE INPUT FIELDS: Removed 'readonly=True' to allow manual in-app creation
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    mark = fields.Char(string='Mark', required=True)
    goods_description = fields.Char(string='Goods Description', required=True)
    qty = fields.Float(string='QTY', required=True)
    cbm_line = fields.Float(string='CBM / Line', required=True)
    
    ins_cbm = fields.Float(string='INS CBM (m³)', default=0.0)
    bgda = fields.Float(string='BGDA (GNF)', default=0.0)

    # 2. CALCULATED PRO-RATA FIELDS (Staging for Invoices) - Kept Read-Only
    prorata_freight_usd = fields.Float(string='Pro-rata Freight (USD)', readonly=True)
    calculated_ins_usd = fields.Float(string='Prorata INS Fee (USD)', readonly=True)
    calculated_usd = fields.Float(string='Total USD', readonly=True) # Sum of Freight + INS
    calculated_gnf = fields.Float(string='Pro-rata Customs (GNF)', readonly=True)

    total_gnf = fields.Float(string='Total GNF', readonly=True)

    # 3. TRACEABILITY LINKS - Kept Read-Only
    usd_invoice_id = fields.Many2one('ogi.transit.invoice', string='USD Invoice', readonly=True, ondelete='set null')
    gnf_invoice_id = fields.Many2one('ogi.transit.invoice', string='GNF Invoice', readonly=True, ondelete='set null')

    # 4. BACKEND SECURITY CHECK: Updated to respect the new Validation workflow
    def write(self, vals):
        input_fields = {'partner_id', 'mark', 'goods_description', 'qty', 'cbm_line', 'ins_cbm', 'bgda'}
        
        # Check if the user is attempting to modify any of the input fields
        if any(field in vals for field in input_fields):
            for line in self:
                # Block manual edits ONLY if the packing list is already validated or the container is closed
                if line.container_id.packing_list_state == 'validated' or line.container_id.state == 'closed':
                    raise ValidationError(_(
                        "Security Restriction: Packing List data cannot be modified after it has been Validated. "
                        "If corrections are needed, the container must be unlocked or un-validated by a Manager."
                    ))
                
        # Allow the write to proceed
        return super().write(vals)
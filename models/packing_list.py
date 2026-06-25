from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

class OgiTransitPLLine(models.Model):
    _name = 'ogi.transit.pl.line'
    _description = 'Packing List Line'
    _order = 'id asc'

    container_id = fields.Many2one('ogi.transit.container', string='Container', required=True, ondelete='cascade')
    
    # 1. CORE IMPORTED FIELDS: Set to Read-Only Mode (TC-US4.4-01)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, readonly=True)
    mark = fields.Char(string='Mark', required=True, readonly=True)
    goods_description = fields.Char(string='Goods Description', required=True, readonly=True)
    qty = fields.Float(string='QTY', required=True, readonly=True)
    cbm_line = fields.Float(string='CBM / Line', required=True, readonly=True)
    
    # UPDATED: Changed from ins_fee to ins_cbm to match the new Excel format
    ins_cbm = fields.Float(string='INS CBM (m³)', default=0.0, readonly=True)
    bgda = fields.Float(string='BGDA (GNF)', default=0.0, readonly=True)

    # 2. CALCULATED PRO-RATA FIELDS (Staging for Invoices)
    prorata_freight_usd = fields.Float(string='Pro-rata Freight (USD)', readonly=True)
    calculated_ins_usd = fields.Float(string='Prorata INS Fee (USD)', readonly=True) # NEW
    calculated_usd = fields.Float(string='Total USD', readonly=True) # Sum of Freight + INS
    calculated_gnf = fields.Float(string='Pro-rata Customs (GNF)', readonly=True)

    total_gnf = fields.Float(string='Total GNF', readonly=True) # NEW FIELD

    # 3. TRACEABILITY LINKS
    usd_invoice_id = fields.Many2one('ogi.transit.invoice', string='USD Invoice', readonly=True, ondelete='set null')
    gnf_invoice_id = fields.Many2one('ogi.transit.invoice', string='GNF Invoice', readonly=True, ondelete='set null')

    # 4. BACKEND SECURITY CHECK: Prohibit Manual Edits (TC-US4.4-02)
    def write(self, vals):
        # UPDATED: Replaced 'ins_fee' with 'ins_cbm' in the locked fields list
        imported_fields = {'partner_id', 'mark', 'goods_description', 'qty', 'cbm_line', 'ins_cbm', 'bgda'}
        
        # Check if the user/system is attempting to modify any of the imported fields
        if any(field in vals for field in imported_fields):
            raise ValidationError(_(
                "Security Restriction: Imported Packing List data is strictly locked to preserve financial integrity. "
                "To make corrections, please update your Excel file and re-import it into the container."
            ))
            
        # Allow the write to proceed if it's just system-calculated fields (like invoices or prorata math)
        return super().write(vals)
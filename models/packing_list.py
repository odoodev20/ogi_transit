from odoo import models, fields, api
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
    ins_fee = fields.Float(string='INS Fee (USD)', default=0.0, readonly=True)
    bgda = fields.Float(string='BGDA (GNF)', default=0.0, readonly=True)

    # Calculated Pro-rata fields (Staging for Invoices)
    calculated_usd = fields.Float(string='Pro-rata Freight (USD)', readonly=True)
    calculated_gnf = fields.Float(string='Pro-rata Customs (GNF)', readonly=True)

    # Traceability links to generated invoices
    usd_invoice_id = fields.Many2one('ogi.transit.invoice', string='USD Invoice', readonly=True, ondelete='set null')
    gnf_invoice_id = fields.Many2one('ogi.transit.invoice', string='GNF Invoice', readonly=True, ondelete='set null')

    # 2. BACKEND SECURITY CHECK: Prohibit Manual Edits (TC-US4.4-02)
    def write(self, vals):
        # List of fields that originate from the Excel file
        imported_fields = {'partner_id', 'mark', 'goods_description', 'qty', 'cbm_line', 'ins_fee', 'bgda'}
        
        # Check if the user/system is attempting to modify any of the imported fields
        if any(field in vals for field in imported_fields):
            raise ValidationError(
                "Security Restriction: Imported Packing List data is strictly locked to preserve financial integrity. "
                "To make corrections, please update your Excel file and re-import it into the container."
            )
            
        # Allow the write to proceed if it's just system-calculated fields (like invoices or prorata math)
        return super().write(vals)
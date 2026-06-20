from odoo import models, fields, api
from odoo.exceptions import ValidationError

class OgiTransitPLLine(models.Model):
    _name = 'ogi.transit.pl.line'
    _description = 'Packing List Line'
    _order = 'id asc'

    container_id = fields.Many2one('ogi.transit.container', string='Container', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Customer', required=True)
    
    # Core Excel Data
    mark = fields.Char(string='Mark', required=True)
    goods_description = fields.Char(string='Goods Description', required=True)
    qty = fields.Float(string='QTY', required=True)
    cbm_line = fields.Float(string='CBM / Line', required=True)
    
    # Additional Fees
    ins_fee = fields.Float(string='INS Fee (USD)', default=0.0)
    bgda = fields.Float(string='BGDA (GNF)', default=0.0)

    # Calculated Pro-rata fields (Staging for Invoices)
    calculated_usd = fields.Float(string='Pro-rata Freight (USD)', readonly=True)
    calculated_gnf = fields.Float(string='Pro-rata Customs (GNF)', readonly=True)

    # Traceability links to generated invoices
    usd_invoice_id = fields.Many2one('ogi.transit.invoice', string='USD Invoice', readonly=True, ondelete='set null')
    gnf_invoice_id = fields.Many2one('ogi.transit.invoice', string='GNF Invoice', readonly=True, ondelete='set null')
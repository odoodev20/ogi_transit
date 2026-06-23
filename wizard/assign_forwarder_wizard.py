from odoo import models, fields, api, _

class AssignForwarderWizard(models.TransientModel):
    _name = 'ogi.transit.assign.forwarder.wizard'
    _description = 'Bulk Assign Forwarder'

    forwarder_id = fields.Many2one(
        'res.partner', 
        string='Freight Forwarder', 
        domain="[('contact_type', '=', 'freight_forwarder')]", 
        required=True
    )

    def action_apply(self):
        active_ids = self.env.context.get('active_ids', [])
        if active_ids:
            containers = self.env['ogi.transit.container'].browse(active_ids)
            containers.write({'forwarder_id': self.forwarder_id.id})
from odoo import models, fields
from odoo.exceptions import AccessError
from markupsafe import Markup

class OgiReasonWizard(models.TransientModel):
    _name = 'ogi.reason.wizard'
    _description = 'Capture Mandatory Reason'

    reason = fields.Text(string='Mandatory Reason', required=True)
    transaction_id = fields.Many2one('ogi.transit.transaction', string='Transaction')

    def action_confirm_cancel(self):
        if not self.env.user.has_group('ogi_transit.group_ogi_pdg') and not self.env.user.has_group('ogi_transit.group_ogi_gerant'):
            raise AccessError("Access Denied: Only a Manager or the CEO can cancel a confirmed transaction.")

        if self.transaction_id:
            log_message = Markup("<strong>Action:</strong> Transaction Cancelled<br/><strong>Reason:</strong> <em>%s</em>") % self.reason
            self.transaction_id.message_post(body=log_message)

        self.transaction_id.write({'state': 'cancelled'})
from odoo import models, fields
from odoo.exceptions import AccessError
from markupsafe import Markup

class OgiReasonWizard(models.TransientModel):
    _name = 'ogi.reason.wizard'
    _description = 'Capture Mandatory Reason'

    reason = fields.Text(string='Mandatory Reason', required=True)
    
    # Links to the documents that can be canceled
    transaction_id = fields.Many2one('ogi.transit.transaction', string='Transaction')
    invoice_id = fields.Many2one('ogi.transit.invoice', string='Invoice') # NEW

    def action_confirm_cancel(self):
        # 1. Strict Security Check
        if not self.env.user.has_group('ogi_transit.group_ogi_pdg') and not self.env.user.has_group('ogi_transit.group_ogi_gerant'):
            raise AccessError("Access Denied: Only a Manager or the CEO can cancel this document.")
            
        # 2. Handle Transaction Cancellations
        if self.transaction_id:
            log_message = Markup(f"<strong>Action:</strong> Transaction Cancelled<br/><strong>Reason:</strong> <em>{self.reason}</em>")
            self.transaction_id.message_post(body=log_message)
            self.transaction_id.write({'state': 'cancelled'})
            
        # 3. NEW: Handle Invoice Cancellations
        if self.invoice_id:
            log_message = Markup(f"<strong>Action:</strong> Invoice Cancelled<br/><strong>Reason:</strong> <em>{self.reason}</em>")
            self.invoice_id.message_post(body=log_message)
            self.invoice_id.write({'state': 'canceled'})
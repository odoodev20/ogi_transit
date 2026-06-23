from odoo import models, fields, api, _
from odoo.exceptions import AccessError
from markupsafe import Markup

class OgiReasonWizard(models.TransientModel):
    _name = 'ogi.reason.wizard'
    _description = 'Capture Mandatory Reason'

    reason = fields.Text(string='Mandatory Reason', required=True)
    
    # Links to the documents that can be acted upon
    transaction_id = fields.Many2one('ogi.transit.transaction', string='Transaction')
    invoice_id = fields.Many2one('ogi.transit.invoice', string='Invoice')
    
    # NEW: Link for Delivery Notes
    delivery_id = fields.Many2one('ogi.transit.delivery.note', string='Delivery Note')

    def action_confirm_cancel(self):
        # 1. Handle Delivery Authorizations (NEW)
        if self.delivery_id:
            if not self.env.user.has_group('ogi_transit.group_ogi_pdg') and not self.env.user.has_group('ogi_transit.group_ogi_gerant'):
                raise AccessError("Access Denied: Only a Manager or CEO can authorize the delivery of unpaid goods.")
                
            self.delivery_id.write({
                'is_authorized': True,
                'authorization_reason': self.reason,
                'authorized_by_id': self.env.user.id
            })
            
            log_message = f"<strong>⚠️ Unpaid Delivery Authorized</strong><br/><strong>By:</strong> {self.env.user.name}<br/><strong>Reason:</strong> <em>{self.reason}</em>"
            self.delivery_id.message_post(body=Markup(log_message))
            return

        # 2. Strict Security Check for Cancellations
        if not self.env.user.has_group('ogi_transit.group_ogi_pdg') and not self.env.user.has_group('ogi_transit.group_ogi_gerant'):
            raise AccessError("Access Denied: Only a Manager or the CEO can cancel this document.")
            
        # 3. Handle Transaction Cancellations
        if self.transaction_id:
            log_message = f"<strong>Action:</strong> Transaction Cancelled<br/><strong>Reason:</strong> <em>{self.reason}</em>"
            self.transaction_id.message_post(body=Markup(log_message))
            self.transaction_id.write({'state': 'cancelled'})
            
        # 4. Handle Invoice Cancellations & Auto-Refunds
        if self.invoice_id:
            inv = self.invoice_id
            refunded_amount = inv.amount_paid
            
            # Auto-refund to customer wallet if payments exist
            if refunded_amount > 0:
                if inv.currency == 'USD':
                    inv.partner_id.deposit_usd += refunded_amount
                else:
                    inv.partner_id.deposit_gnf += refunded_amount
                
                # Unmatch the payments (Reset to 0)
                inv.amount_paid = 0.0
            
            # Log the cancellation and the refund in the chatter
            log_message = f"<strong>Action:</strong> Invoice Cancelled<br/><strong>Reason:</strong> <em>{self.reason}</em>"
            if refunded_amount > 0:
                log_message += f"<br/><strong>Auto-Refund:</strong> {refunded_amount} {inv.currency} has been credited back to the customer's wallet."
            
            inv.message_post(body=Markup(log_message))
            inv.write({'state': 'canceled'})
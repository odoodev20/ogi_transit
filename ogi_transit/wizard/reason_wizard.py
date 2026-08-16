from odoo import models, fields, api, _
from odoo.exceptions import AccessError
from odoo.exceptions import ValidationError
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
        # BUG FIX: Strict backend validation to ensure the reason is not empty or just spaces
        if not self.reason or not str(self.reason).strip():
            raise ValidationError(_("Validation Error: You must provide a mandatory reason for this action."))

        # 1. Handle Delivery Authorizations (NEW)
        if self.delivery_id:
            if not self.env.user.has_group('ogi_transit.group_ogi_pdg') and not self.env.user.has_group('ogi_transit.group_ogi_gerant'):
                raise AccessError(_("Access Denied: Only a Manager or CEO can authorize the delivery of unpaid goods."))
                
            self.delivery_id.write({
                'is_authorized': True,
                'authorization_reason': self.reason,
                'authorized_by_id': self.env.user.id
            })
            
            log_message = _("<strong>⚠️ Unpaid Delivery Authorized</strong><br/><strong>By:</strong> %s<br/><strong>Reason:</strong> <em>%s</em>") % (self.env.user.name, self.reason)
            self.delivery_id.message_post(body=Markup(log_message))
            return

        # 2. Strict Security Check for Cancellations
        if not self.env.user.has_group('ogi_transit.group_ogi_pdg') and not self.env.user.has_group('ogi_transit.group_ogi_gerant'):
            raise AccessError(_("Access Denied: Only a Manager or the CEO can cancel this document."))
            
        # 3. Handle Transaction Cancellations
        if self.transaction_id:
            log_message = _("<strong>Action:</strong> Transaction Cancelled<br/><strong>Reason:</strong> <em>%s</em>") % self.reason
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
            log_message = _("<strong>Action:</strong> Invoice Cancelled<br/><strong>Reason:</strong> <em>%s</em>") % self.reason
            if refunded_amount > 0:
                log_message += _("<br/><strong>Auto-Refund:</strong> %s %s has been credited back to the customer's wallet.") % (refunded_amount, inv.currency)
            
            inv.message_post(body=Markup(log_message))
            inv.write({'state': 'canceled'})
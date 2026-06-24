from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from markupsafe import Markup

class OgiInvoiceReminderWizard(models.TransientModel):
    _name = 'ogi.invoice.reminder.wizard'
    _description = 'Log Customer Reminder'

    invoice_id = fields.Many2one('ogi.transit.invoice', string='Invoice', required=True)
    call_date = fields.Date(string='Call Date', default=fields.Date.context_today, required=True)
    
    # NEW: The Status Field requested in the bug
    result = fields.Selection([
        ('no_answer', 'No Answer / Unreachable'),
        ('promise', 'Payment Promise'),
        ('refusal', 'Refusal / Dispute')
    ], string='Call Result (Status)', required=True, default='promise')

    promise_date = fields.Date(string='Promise to Pay Date')
    notes = fields.Text(string='Summary / Notes', required=True)

    # BUG FIX: Strictly enforce the validation rule for Payment Promise
    @api.constrains('result', 'promise_date')
    def _check_promise_date(self):
        for wiz in self:
            if wiz.result == 'promise' and not wiz.promise_date:
                # REFACTORED: Wrapped in _()
                raise ValidationError(_("Validation Error: You must enter a 'Promise to Pay Date' when the status is 'Payment Promise'."))

    def action_log_reminder(self):
        for wiz in self:
            # 1. Update the tracking fields on the Invoice
            wiz.invoice_id.last_call_date = wiz.call_date
            
            if wiz.result == 'promise':
                wiz.invoice_id.promise_to_pay_date = wiz.promise_date
            else:
                wiz.invoice_id.promise_to_pay_date = False # Clear old promises if they refused/didn't answer
            
            # 2. Append the new notes with a timestamp and status
            status_label = dict(self._fields['result'].selection).get(wiz.result)
            # REFACTORED: Converted f-string to %s formatting
            new_note = _("[%s] %s: %s") % (wiz.call_date, status_label, wiz.notes)
            
            if wiz.invoice_id.collection_notes:
                wiz.invoice_id.collection_notes = "%s\n%s" % (wiz.invoice_id.collection_notes, new_note)
            else:
                wiz.invoice_id.collection_notes = new_note
            
            # 3. Log securely to the chatter
            # REFACTORED: Converted f-string to %s formatting and wrapped in _()
            wiz.invoice_id.message_post(body=Markup(_("<strong>Reminder Logged:</strong> %s<br/>%s") % (status_label, wiz.notes)))
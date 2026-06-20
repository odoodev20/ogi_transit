from odoo import models, fields, api
from datetime import timedelta

class OgiTransitDashboard(models.Model):
    _name = 'ogi.transit.dashboard'
    _description = 'Master KPI Dashboard'

    name = fields.Char(default='Master Dashboard')
    
    # 1. System Config & Alerts (Admin/Finance/Managers)
    audit_days_threshold = fields.Integer(string="Audit Alert Threshold (Days)", default=3, help="Set by Admin")
    cashbox_alert_message = fields.Char(compute='_compute_alerts')

    # 2. Logistics KPIs
    active_containers = fields.Integer(compute='_compute_kpis', string="Active Containers")
    balance_cargo_china = fields.Integer(compute='_compute_kpis', string="Active Cargo (China)")
    balance_cargo_dubai = fields.Integer(compute='_compute_kpis', string="Active Cargo (Dubai)")
    pending_deliveries = fields.Integer(compute='_compute_kpis', string="Pending Deliveries")
    
    # 3. Financial KPIs (Cash & Debt)
    total_cash_usd = fields.Float(compute='_compute_kpis', string="Total Vault USD")
    total_cash_gnf = fields.Float(compute='_compute_kpis', string="Total Vault GNF")
    unpaid_invoices_usd = fields.Float(compute='_compute_kpis', string="Unpaid Customer Debt (USD)")
    unpaid_invoices_gnf = fields.Float(compute='_compute_kpis', string="Unpaid Customer Debt (GNF)")
    
    # 4. Sensitive Financial KPIs (Margins & Vendors)
    marge_brute_gnf = fields.Float(compute='_compute_kpis', string="Gross Margin (GNF)")
    transitaires_non_soldes = fields.Integer(compute='_compute_kpis', string="Unpaid Freight Forwarders")

    # 5. Quick Access Lists (Many2many relations for the UI)
    recent_container_ids = fields.Many2many('ogi.transit.container', compute='_compute_lists')
    draft_invoice_ids = fields.Many2many('ogi.transit.invoice', compute='_compute_lists')
    terrain_delivery_ids = fields.Many2many('ogi.transit.delivery.note', compute='_compute_lists')

    def _compute_kpis(self):
        for record in self:
            # We use sudo() so the background math never throws access errors for lower-level roles
            Container = self.env['ogi.transit.container'].sudo()
            Delivery = self.env['ogi.transit.delivery.note'].sudo()
            Cashbox = self.env['ogi.transit.cashbox'].sudo()
            Invoice = self.env['ogi.transit.invoice'].sudo()
            VendorBill = self.env['ogi.transit.vendor.bill'].sudo()

            # Container Math
            containers = Container.search([('state', '!=', 'closed')])
            record.active_containers = len(containers.filtered(lambda c: c.state != 'released'))
            record.balance_cargo_china = len(containers.filtered(lambda c: c.origin == 'china'))
            record.balance_cargo_dubai = len(containers.filtered(lambda c: c.origin == 'dubai'))
            
            # Delivery Math
            record.pending_deliveries = Delivery.search_count([('logistics_status', 'in', ['pending', 'unpacked', 'storage'])])
            
            # Cash Math
            usd_boxes = Cashbox.search([('currency', '=', 'USD')])
            gnf_boxes = Cashbox.search([('currency', '=', 'GNF')])
            record.total_cash_usd = sum(usd_boxes.mapped('balance'))
            record.total_cash_gnf = sum(gnf_boxes.mapped('balance'))
            
            # Debt Math
            usd_invs = Invoice.search([('currency', '=', 'USD'), ('state', '!=', 'paid')])
            gnf_invs = Invoice.search([('currency', '=', 'GNF'), ('state', '!=', 'paid')])
            try:
                record.unpaid_invoices_usd = sum(usd_invs.mapped('amount_residual'))
                record.unpaid_invoices_gnf = sum(gnf_invs.mapped('amount_residual'))
            except AttributeError:
                record.unpaid_invoices_usd = sum(usd_invs.mapped(lambda i: i.amount_total - i.amount_paid if hasattr(i, 'amount_paid') else i.amount_total))
                record.unpaid_invoices_gnf = sum(gnf_invs.mapped(lambda i: i.amount_total - i.amount_paid if hasattr(i, 'amount_paid') else i.amount_total))
            
            # Gross Margin Math (Only Active Home Containers)
            home_containers = containers.filtered(lambda c: c.type in ['fcl_home', 'lcl_home'] and c.state not in ['released', 'closed'])
            record.marge_brute_gnf = sum(home_containers.mapped('total_customs_gnf')) - sum(home_containers.mapped('total_freight_forwarder_gnf'))
            
            # Unpaid Freight Forwarders Math
            record.transitaires_non_soldes = VendorBill.search_count([
                ('expense_type', '=', 'forwarder'),
                ('state', '=', 'issued')
            ])

    def _compute_lists(self):
        for record in self:
            Container = self.env['ogi.transit.container'].sudo()
            Invoice = self.env['ogi.transit.invoice'].sudo()
            Delivery = self.env['ogi.transit.delivery.note'].sudo()
            
            record.recent_container_ids = Container.search([], order='create_date desc', limit=10).ids
            record.draft_invoice_ids = Invoice.search([('state', '=', 'draft')], order='create_date desc', limit=15).ids
            record.terrain_delivery_ids = Delivery.search([('logistics_status', 'in', ['unpacked', 'storage'])], order='create_date desc').ids

    def _compute_alerts(self):
        for record in self:
            Cashbox = self.env['ogi.transit.cashbox'].sudo()
            Audit = self.env['ogi.transit.cash.audit'].sudo()
            
            threshold_date = fields.Date.today() - timedelta(days=record.audit_days_threshold)
            boxes = Cashbox.search([])
            alert_boxes = []
            for box in boxes:
                last_audit = Audit.search([('cashbox_id', '=', box.id), ('state', '=', 'validated')], order='date desc', limit=1)
                if not last_audit or last_audit.date < threshold_date:
                    alert_boxes.append(box.name)
            
            if alert_boxes:
                record.cashbox_alert_message = f"ALERT: The following cash registers have not been audited in {record.audit_days_threshold} days: {', '.join(alert_boxes)}"
            else:
                record.cashbox_alert_message = False

    @api.model
    def get_master_dashboard(self):
        dashboard = self.search([], limit=1)
        if not dashboard:
            dashboard = self.sudo().create({'name': 'HQ Master Dashboard'})
        return {
            'type': 'ir.actions.act_window',
            'name': 'Command Center',
            'res_model': 'ogi.transit.dashboard',
            'res_id': dashboard.id,
            'view_mode': 'form',
            'target': 'current',
        }
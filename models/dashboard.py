from odoo import models, fields, api, _
from datetime import timedelta

class OgiTransitDashboard(models.Model):
    _name = 'ogi.transit.dashboard'
    _description = 'Master KPI Dashboard'

    name = fields.Char(default='Master Dashboard')
    
    # 1. System Config & Alerts
    audit_days_threshold = fields.Integer(string="Audit Alert Threshold (Days)", default=3, help="Set by Admin")
    cashbox_alert_message = fields.Char(compute='_compute_alerts')

    # 2. Logistics KPIs
    active_containers = fields.Integer(compute='_compute_kpis', string="Active Containers")
    pending_deliveries = fields.Integer(compute='_compute_kpis', string="Goods in Field")
    
    # 3. Cargo Balances (Detailed Breakdowns)
    cargo_china_collected = fields.Float(compute='_compute_kpis')
    cargo_china_usd = fields.Float(compute='_compute_kpis')
    cargo_china_transfer = fields.Float(compute='_compute_kpis')
    cargo_china_variance = fields.Float(compute='_compute_kpis')
    
    cargo_dubai_collected = fields.Float(compute='_compute_kpis')
    cargo_dubai_usd = fields.Float(compute='_compute_kpis')
    cargo_dubai_transfer = fields.Float(compute='_compute_kpis')
    cargo_dubai_variance = fields.Float(compute='_compute_kpis')

    # 4. Vault Breakdowns
    vault_usd_china = fields.Float(compute='_compute_kpis')
    vault_usd_dubai = fields.Float(compute='_compute_kpis')
    vault_gnf_china = fields.Float(compute='_compute_kpis')
    vault_gnf_dubai = fields.Float(compute='_compute_kpis')
    
    # 5. Debt & Margins
    unpaid_invoices_usd = fields.Float(compute='_compute_kpis', string="Unpaid USD")
    unpaid_invoices_gnf = fields.Float(compute='_compute_kpis', string="Unpaid GNF")
    marge_brute_gnf = fields.Float(compute='_compute_kpis', string="Gross Margin (GNF)")
    
    transitaires_non_soldes = fields.Integer(compute='_compute_kpis', string="Unpaid Freight Forwarders")
    ff_partially_paid = fields.Integer(compute='_compute_kpis', string="Partially Paid FF")

    recent_container_ids = fields.Many2many('ogi.transit.container', compute='_compute_lists')
    draft_invoice_ids = fields.Many2many('ogi.transit.invoice', compute='_compute_lists')
    terrain_delivery_ids = fields.Many2many('ogi.transit.delivery.note', compute='_compute_lists')

    def _compute_kpis(self):
        for record in self:
            Container = self.env['ogi.transit.container'].sudo()
            Delivery = self.env['ogi.transit.delivery.note'].sudo()
            Cashbox = self.env['ogi.transit.cashbox'].sudo()
            Invoice = self.env['ogi.transit.invoice'].sudo()
            VendorBill = self.env['ogi.transit.vendor.bill'].sudo()

            # --- LOGISTICS ---
            # Active Containers (!= Closed)
            containers = Container.search([('state', '!=', 'closed')])
            record.active_containers = len(containers)
            
            # Goods in the Field
            record.pending_deliveries = Delivery.search_count([('logistics_status', '!=', 'retrieved')])
            
            # --- VAULTS ---
            box_usd_china = Cashbox.search([('currency', '=', 'USD'), ('origin', '=', 'china'), ('name', 'not ilike', 'Transfer')], limit=1)
            box_usd_dubai = Cashbox.search([('currency', '=', 'USD'), ('origin', '=', 'dubai'), ('name', 'not ilike', 'Transfer')], limit=1)
            box_gnf_china = Cashbox.search([('currency', '=', 'GNF'), ('origin', '=', 'china')], limit=1)
            box_gnf_dubai = Cashbox.search([('currency', '=', 'GNF'), ('origin', '=', 'dubai')], limit=1)
            
            record.vault_usd_china = box_usd_china.balance if box_usd_china else 0.0
            record.vault_usd_dubai = box_usd_dubai.balance if box_usd_dubai else 0.0
            record.vault_gnf_china = box_gnf_china.balance if box_gnf_china else 0.0
            record.vault_gnf_dubai = box_gnf_dubai.balance if box_gnf_dubai else 0.0

            # --- CARGO BALANCES ---
            # China
            inv_usd_china = Invoice.search([('currency', '=', 'USD'), ('container_id.origin', '=', 'china'), ('state', '!=', 'canceled')])
            box_trans_china = Cashbox.search([('name', '=', 'China Transfer Register')], limit=1)
            
            record.cargo_china_collected = sum(inv_usd_china.mapped('amount_paid'))
            record.cargo_china_usd = record.vault_usd_china
            record.cargo_china_transfer = box_trans_china.balance if box_trans_china else 0.0
            record.cargo_china_variance = record.cargo_china_collected - (record.cargo_china_usd + record.cargo_china_transfer)

            # Dubai
            inv_usd_dubai = Invoice.search([('currency', '=', 'USD'), ('container_id.origin', '=', 'dubai'), ('state', '!=', 'canceled')])
            box_trans_dubai = Cashbox.search([('name', '=', 'Dubai Transfer Register')], limit=1)
            
            record.cargo_dubai_collected = sum(inv_usd_dubai.mapped('amount_paid'))
            record.cargo_dubai_usd = record.vault_usd_dubai
            record.cargo_dubai_transfer = box_trans_dubai.balance if box_trans_dubai else 0.0
            record.cargo_dubai_variance = record.cargo_dubai_collected - (record.cargo_dubai_usd + record.cargo_dubai_transfer)

            # --- DEBT & MARGINS ---
            usd_invs = Invoice.search([('currency', '=', 'USD'), ('state', 'not in', ['paid', 'canceled'])])
            gnf_invs = Invoice.search([('currency', '=', 'GNF'), ('state', 'not in', ['paid', 'canceled'])])
            
            record.unpaid_invoices_usd = sum(usd_invs.mapped('amount_residual'))
            record.unpaid_invoices_gnf = sum(gnf_invs.mapped('amount_residual'))
            
            home_containers = containers.filtered(lambda c: c.type in ['fcl_home', 'lcl_home'])
            record.marge_brute_gnf = sum(home_containers.mapped('total_customs_gnf')) - sum(home_containers.mapped('total_freight_forwarder_gnf'))
            
            # Vendor Bills (Freight Forwarders)
            record.transitaires_non_soldes = VendorBill.search_count([('partner_id.contact_type', '=', 'freight_forwarder'), ('state', '=', 'issued')])
            record.ff_partially_paid = VendorBill.search_count([('partner_id.contact_type', '=', 'freight_forwarder'), ('state', '=', 'partial')])

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
                record.cashbox_alert_message = _("ALERT: The following cash registers have not been audited in %s days: %s") % (record.audit_days_threshold, ', '.join(alert_boxes))
            else:
                record.cashbox_alert_message = False

    @api.model
    def get_master_dashboard(self):
        dashboard = self.search([], limit=1)
        if not dashboard:
            dashboard = self.sudo().create({'name': _('HQ Master Dashboard')})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Command Center'),
            'res_model': 'ogi.transit.dashboard',
            'res_id': dashboard.id,
            'view_mode': 'form',
            'target': 'current',
        }

    # ==========================================
    # ACTION METHODS FOR CLICKABLE KPI REDIRECTS
    # ==========================================
    def action_open_active_containers(self):
        return {
            'name': _('Active Containers'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.container',
            'view_mode': 'list,form',
            'domain': [('state', '!=', 'closed')],
        }

    def action_open_goods_in_field(self):
        return {
            'name': _('Goods in the Field'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.delivery.note',
            'view_mode': 'list,form',
            'domain': [('logistics_status', '!=', 'retrieved')],
        }

    def _open_vault(self, currency, origin, name_domain=None):
        domain = [('currency', '=', currency), ('origin', '=', origin)]
        if name_domain:
            domain.append(name_domain)
        return {
            'name': _('%s %s Register') % (currency, origin.capitalize()),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.cashbox',
            'view_mode': 'list,form',
            'domain': domain,
        }

    def action_open_vault_usd_china(self):
        return self._open_vault('USD', 'china', ('name', 'not ilike', 'Transfer'))
        
    def action_open_vault_usd_dubai(self):
        return self._open_vault('USD', 'dubai', ('name', 'not ilike', 'Transfer'))

    def action_open_vault_gnf_china(self):
        return self._open_vault('GNF', 'china')

    def action_open_vault_gnf_dubai(self):
        return self._open_vault('GNF', 'dubai')

    def _open_unpaid_invoices(self, currency):
        return {
            'name': _('Unpaid Invoices (%s)') % currency,
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.invoice',
            'view_mode': 'list,form',
            'domain': [('currency', '=', currency), ('state', 'not in', ['paid', 'canceled'])],
        }

    def action_open_unpaid_usd(self):
        return self._open_unpaid_invoices('USD')

    def action_open_unpaid_gnf(self):
        return self._open_unpaid_invoices('GNF')

    def action_open_unpaid_ff(self):
        return {
            'name': _('Unpaid Freight Forwarders'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.vendor.bill',
            'view_mode': 'list,form',
            'domain': [('partner_id.contact_type', '=', 'freight_forwarder'), ('state', '=', 'issued')],
        }

    def action_open_partial_ff(self):
        return {
            'name': _('Partially Paid Freight Forwarders'),
            'type': 'ir.actions.act_window',
            'res_model': 'ogi.transit.vendor.bill',
            'view_mode': 'list,form',
            'domain': [('partner_id.contact_type', '=', 'freight_forwarder'), ('state', '=', 'partial')],
        }
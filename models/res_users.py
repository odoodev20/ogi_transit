from odoo import models, fields, api
from markupsafe import Markup

# 1. NEW MODEL: Dedicated Role Audit Log Table
class OgiTransitRoleLog(models.Model):
    _name = 'ogi.transit.role.log'
    _description = 'User Role Audit Log'
    _order = 'create_date desc'

    user_id = fields.Many2one('res.users', string='User', required=True, ondelete='cascade')
    admin_id = fields.Many2one('res.users', string='Changed By')
    details = fields.Html(string='Role Changes')

# 2. UPDATE EXISTING: Attach the log to the User
class ResUsers(models.Model):
    _inherit = 'res.users'

    ogi_role_log_ids = fields.One2many('ogi.transit.role.log', 'user_id', string='Role Audit Trail')

    def write(self, vals):
        ogi_category = self.env.ref('ogi_transit.module_category_ogi_transit', raise_if_not_found=False)
        
        # 1. SNAPSHOT BEFORE SAVE: Capture existing OGI roles
        old_groups_map = {}
        if ogi_category:
            for user in self:
                old_groups = user.groups_id.filtered(lambda g: g.category_id == ogi_category)
                old_groups_map[user.id] = set(old_groups.ids)

        # 2. Perform the standard database write (Odoo handles the in_group_XXX checkboxes here)
        res = super().write(vals)

        # 3. SNAPSHOT AFTER SAVE: Compare and log differences
        if ogi_category:
            for user in self:
                old_ids = old_groups_map.get(user.id, set())
                new_groups = user.groups_id.filtered(lambda g: g.category_id == ogi_category)
                new_ids = set(new_groups.ids)

                added_ids = new_ids - old_ids
                removed_ids = old_ids - new_ids

                # If there is a change, construct the log entry
                if added_ids or removed_ids:
                    Group = self.env['res.groups']
                    msg = "<ul style='margin-bottom: 0; padding-left: 20px;'>"
                    
                    if added_ids:
                        added_names = Group.browse(list(added_ids)).mapped('name')
                        msg += f"<li><span style='color: #28a745;'><strong>Added:</strong> {', '.join(added_names)}</span></li>"
                        
                    if removed_ids:
                        removed_names = Group.browse(list(removed_ids)).mapped('name')
                        msg += f"<li><span style='color: #dc3545;'><strong>Removed:</strong> {', '.join(removed_names)}</span></li>"
                        
                    msg += "</ul>"
                    
                    # .sudo() safely bypasses security rules to ensure the log is always created
                    self.env['ogi.transit.role.log'].sudo().create({
                        'user_id': user.id,
                        'admin_id': self.env.user.id,
                        'details': Markup(msg)
                    })

        return res
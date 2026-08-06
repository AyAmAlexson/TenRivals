"""Container for buying.* permissions (no table; Django auth framework only).

Superusers pass every check automatically; permissions can later be granted to
non-superuser staff via groups without changing any view code.
"""

from django.db import models


class BuyingPermissions(models.Model):
    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ('view', 'Can view buying section'),
            ('create', 'Can create buying requests'),
            ('run_search', 'Can run supplier search'),
            ('retry_connectors', 'Can retry failed connectors'),
            ('confirm_mapping', 'Can confirm product mappings'),
            ('override_calculation', 'Can override calculations'),
            ('manage_suppliers', 'Can manage suppliers'),
            ('manage_fulfillment_routes', 'Can manage fulfillment routes'),
            ('manage_pricing_rules', 'Can manage pricing rules'),
            ('manage_optimization_rules', 'Can manage optimization rules'),
        ]

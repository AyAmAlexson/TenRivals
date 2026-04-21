"""Align django.contrib.sites.Site (pk=SITE_ID) with SITE_DOMAIN / SITE_DISPLAY_NAME."""

from django.conf import settings
from django.db import migrations


def _normalize_domain(raw: str) -> str:
    s = (raw or '').strip().lower()
    for prefix in ('https://', 'http://'):
        if s.startswith(prefix):
            s = s[len(prefix) :]
    return s.split('/')[0].split(':')[0] or 'tenrivals.com'


def forwards(apps, schema_editor):
    Site = apps.get_model('sites', 'Site')
    site_id = int(getattr(settings, 'SITE_ID', 1))
    domain = _normalize_domain(getattr(settings, 'SITE_DOMAIN', 'tenrivals.com'))
    name = (getattr(settings, 'SITE_DISPLAY_NAME', None) or 'Tennis Rivals').strip() or 'Tennis Rivals'
    Site.objects.update_or_create(
        pk=site_id,
        defaults={'domain': domain, 'name': name},
    )


class Migration(migrations.Migration):
    dependencies = [
        ('sites', '0002_alter_domain_unique'),
        ('shop', '0032_order_for_me'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]

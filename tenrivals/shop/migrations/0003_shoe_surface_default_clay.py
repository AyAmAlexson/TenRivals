from django.db import migrations


def set_clay_surface(apps, schema_editor):
    Shoe = apps.get_model('shop', 'Shoe')
    for s in Shoe.objects.all():
        if not getattr(s, 'surface', None):
            s.surface = 'CL'
            s.save(update_fields=['surface'])


class Migration(migrations.Migration):
    dependencies = [
        ('shop', '0002_product_image_4_product_image_5'),
    ]

    operations = [
        migrations.RunPython(set_clay_surface, reverse_code=migrations.RunPython.noop),
    ]



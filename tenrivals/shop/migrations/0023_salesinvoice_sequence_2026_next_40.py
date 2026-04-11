from django.db import migrations


def set_2026_sequence_next_40(apps, schema_editor):
    SalesInvoiceYearSequence = apps.get_model('shop', 'SalesInvoiceYearSequence')
    SalesInvoiceYearSequence.objects.update_or_create(
        year=2026,
        defaults={'last_seq': 39},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0022_product_color_move_from_shoe'),
    ]

    operations = [
        migrations.RunPython(set_2026_sequence_next_40, migrations.RunPython.noop),
    ]

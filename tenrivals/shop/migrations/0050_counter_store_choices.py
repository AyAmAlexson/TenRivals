from django.db import migrations, models


def seed_counter_stores(apps, schema_editor):
    CounterStore = apps.get_model('shop', 'CounterStore')
    for name, sort_order in (
        ('HQ Warehouse', 1),
        ('City Sport Store', 2),
    ):
        CounterStore.objects.get_or_create(
            name=name,
            defaults={'is_active': True, 'sort_order': sort_order},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0049_product_barcode_and_counter'),
    ]

    operations = [
        migrations.AddField(
            model_name='counterstore',
            name='is_active',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='counterstore',
            name='sort_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name='counterstore',
            options={'ordering': ['sort_order', 'name']},
        ),
        migrations.RunPython(seed_counter_stores, migrations.RunPython.noop),
    ]

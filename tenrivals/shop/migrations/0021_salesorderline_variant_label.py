# Generated manually for per-variant stock (grip / shoe size).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0020_sales_order_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorderline',
            name='variant_label',
            field=models.CharField(
                blank=True,
                max_length=48,
                help_text='Grip size (L2, …) or shoe US size when product uses size grid.',
            ),
        ),
    ]

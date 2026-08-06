from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0044_salesorderline_product_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='source',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Staff-only: acquisition source (UTM, referral, Organic Website, …).',
                max_length=255,
            ),
        ),
        migrations.AddField(
            model_name='customer',
            name='comment',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Staff-only internal comment about this customer.',
            ),
        ),
    ]

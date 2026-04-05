# Generated manually — rename price, add actual_price, ProductListing

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0005_shoporderitem_line_total_default'),
    ]

    operations = [
        migrations.RenameField(
            model_name='product',
            old_name='price',
            new_name='initial_price',
        ),
        migrations.AddField(
            model_name='product',
            name='actual_price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.CreateModel(
            name='ProductListing',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                (
                    'channel',
                    models.CharField(
                        choices=[('STOCK', 'In stock'), ('PREORDER', 'Preorder')],
                        max_length=16,
                    ),
                ),
                ('quantity', models.PositiveIntegerField(default=0)),
                (
                    'product',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='listings',
                        to='shop.product',
                    ),
                ),
            ],
            options={
                'ordering': ['-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='productlisting',
            constraint=models.UniqueConstraint(
                fields=('product', 'channel'),
                name='shop_product_listing_unique_channel',
            ),
        ),
    ]

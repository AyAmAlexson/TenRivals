# Generated manually for promo codes + order discount snapshot

import django.db.models.deletion
from decimal import Decimal

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shop', '0034_blogpost_article_inline_filefield'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorder',
            name='promo_code_label',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Promo code string applied at checkout (snapshot).',
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name='salesorder',
            name='promo_discount_gross',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0.00'),
                help_text='VAT-inclusive promo discount (₾), subtracted from line subtotal.',
                max_digits=12,
            ),
        ),
        migrations.CreateModel(
            name='PromoCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(db_index=True, help_text='Case-insensitive; stored uppercase.', max_length=64, unique=True)),
                ('title', models.CharField(blank=True, help_text='Internal label for staff (optional).', max_length=160)),
                ('is_manually_active', models.BooleanField(db_index=True, default=True, help_text='Turn off to deactivate regardless of dates.')),
                (
                    'discount_type',
                    models.CharField(
                        choices=[('FIXED_GROSS', 'Fixed amount (₾)'), ('PERCENT', 'Percent of eligible subtotal')],
                        default='PERCENT',
                        max_length=16,
                    ),
                ),
                (
                    'fixed_amount_gross',
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text='For fixed type: amount to subtract (₾), capped by eligible subtotal and by % cap below.',
                        max_digits=10,
                        null=True,
                    ),
                ),
                (
                    'fixed_cap_percent_of_eligible',
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal('100.00'),
                        help_text='For fixed type: discount cannot exceed this % of eligible subtotal (default 100).',
                        max_digits=5,
                    ),
                ),
                (
                    'percent_off',
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        help_text='For percent type: 0–100.',
                        max_digits=5,
                        null=True,
                    ),
                ),
                (
                    'percent_min_eligible_subtotal',
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal('0.00'),
                        help_text='For percent type: eligible subtotal must be at least this (₾).',
                        max_digits=10,
                    ),
                ),
                (
                    'valid_from',
                    models.DateTimeField(
                        blank=True,
                        help_text='If set, code is not valid before this instant (site timezone).',
                        null=True,
                    ),
                ),
                (
                    'valid_until',
                    models.DateTimeField(
                        blank=True,
                        help_text='If set, code is not valid after this instant.',
                        null=True,
                    ),
                ),
                (
                    'single_use_globally',
                    models.BooleanField(
                        default=False,
                        help_text='If true, only one redemption ever (across all customers).',
                    ),
                ),
                (
                    'max_redemptions',
                    models.PositiveIntegerField(
                        blank=True,
                        help_text='Optional cap on total redemptions; leave empty for unlimited (within dates).',
                        null=True,
                    ),
                ),
                (
                    'application_scope',
                    models.CharField(
                        choices=[
                            ('ALL', 'Entire catalog'),
                            ('PRODUCTS', 'Specific products (SKU)'),
                            ('COLLECTIONS', 'Specific collections'),
                        ],
                        default='ALL',
                        max_length=16,
                    ),
                ),
                ('sort_order', models.IntegerField(db_index=True, default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'restricted_to_user',
                    models.ForeignKey(
                        blank=True,
                        help_text='If set, only this signed-in user may redeem the code.',
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='assigned_promo_codes',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'verbose_name': 'Promo code',
                'verbose_name_plural': 'Promo codes',
                'ordering': ['sort_order', 'code'],
            },
        ),
        migrations.CreateModel(
            name='PromoRedemption',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('discount_gross', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                (
                    'promo',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='redemptions',
                        to='shop.promocode',
                    ),
                ),
                (
                    'redeemed_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='promo_redemptions',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    'sales_order',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='promo_redemptions',
                        to='shop.salesorder',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Promo redemption',
                'verbose_name_plural': 'Promo redemptions',
                'ordering': ['-id'],
            },
        ),
        migrations.AddField(
            model_name='promocode',
            name='restricted_collections',
            field=models.ManyToManyField(
                blank=True,
                help_text='When scope is “Specific collections” — union of collection + group products.',
                related_name='promo_codes_by_collection',
                to='shop.productcollection',
            ),
        ),
        migrations.AddField(
            model_name='promocode',
            name='restricted_products',
            field=models.ManyToManyField(
                blank=True,
                help_text='When scope is “Specific products” — eligible SKUs.',
                related_name='promo_codes_by_product',
                to='shop.product',
            ),
        ),
    ]

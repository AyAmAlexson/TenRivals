from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0051_stored_image_rendition'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonthLedger',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveSmallIntegerField()),
                ('month', models.PositiveSmallIntegerField()),
                (
                    'tax_credit',
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal('0.00'),
                        help_text='VAT and turnover-tax credit applied to this month (₾).',
                        max_digits=12,
                    ),
                ),
                ('rent', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('communications', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('services', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('accounting', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('payroll', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=12)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-year', '-month'],
            },
        ),
        migrations.AddConstraint(
            model_name='monthledger',
            constraint=models.UniqueConstraint(fields=('year', 'month'), name='shop_monthledger_year_month_uniq'),
        ),
        migrations.AddConstraint(
            model_name='monthledger',
            constraint=models.CheckConstraint(
                check=models.Q(month__gte=1, month__lte=12),
                name='shop_monthledger_month_valid',
            ),
        ),
    ]

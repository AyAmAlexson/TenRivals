from decimal import Decimal

from django.db import migrations, models


def backfill_payment_currency(apps, schema_editor):
    SalesOrder = apps.get_model('shop', 'SalesOrder')
    for order in SalesOrder.objects.all().only('pk', 'gross_total'):
        gross = order.gross_total or Decimal('0')
        SalesOrder.objects.filter(pk=order.pk).update(
            payment_currency='GEL',
            exchange_rate=Decimal('1'),
            amount_in_payment_currency=gross,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0036_homeherocontent_i18n'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesorder',
            name='payment_currency',
            field=models.CharField(
                db_index=True,
                default='GEL',
                help_text='Currency for payment (line totals remain in GEL).',
                max_length=8,
            ),
        ),
        migrations.AddField(
            model_name='salesorder',
            name='exchange_rate',
            field=models.DecimalField(
                decimal_places=6,
                default=Decimal('1'),
                help_text='Multiplier vs GEL: payment amount = gross_total × rate (1 for GEL).',
                max_digits=14,
            ),
        ),
        migrations.AddField(
            model_name='salesorder',
            name='amount_in_payment_currency',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('0'),
                help_text='Order gross total expressed in payment_currency (gross_total × exchange_rate).',
                max_digits=12,
            ),
        ),
        migrations.RunPython(backfill_payment_currency, migrations.RunPython.noop),
    ]

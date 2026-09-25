from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


def backfill_payments(apps, schema_editor):
    """Every existing order becomes one payment on its order_date for the full
    gross (legacy model assumed money and receipt arrive with the order)."""
    SalesOrder = apps.get_model('shop', 'SalesOrder')
    SalesOrderPayment = apps.get_model('shop', 'SalesOrderPayment')
    batch = []
    for o in SalesOrder.objects.all().iterator(chunk_size=500):
        gross = o.gross_total or Decimal('0.00')
        if gross == 0 and not (o.fiscal_receipt or '').strip():
            continue
        batch.append(
            SalesOrderPayment(
                order_id=o.pk,
                paid_on=o.order_date,
                amount_gross=gross,
                payment_method=(o.payment_method or '')[:200],
                fiscal_receipt=(o.fiscal_receipt or '')[:64],
                note='Backfilled from order (pre-payments era)',
            )
        )
        if len(batch) >= 500:
            SalesOrderPayment.objects.bulk_create(batch)
            batch = []
    if batch:
        SalesOrderPayment.objects.bulk_create(batch)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0046_staff_ai_insight'),
    ]

    operations = [
        migrations.AlterField(
            model_name='salesorder',
            name='fiscal_receipt',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.CreateModel(
            name='SalesOrderPayment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('paid_on', models.DateField(db_index=True)),
                ('amount_gross', models.DecimalField(
                    decimal_places=2,
                    help_text='Amount received, VAT-inclusive (₾). Negative for refunds.',
                    max_digits=12,
                )),
                ('payment_method', models.CharField(blank=True, max_length=200)),
                ('fiscal_receipt', models.CharField(blank=True, max_length=64)),
                ('note', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='payments',
                    to='shop.salesorder',
                )),
            ],
            options={
                'ordering': ['paid_on', 'id'],
                'indexes': [models.Index(fields=['paid_on', 'order'], name='shop_saleso_paid_on_729516_idx')],
            },
        ),
        migrations.RunPython(backfill_payments, noop),
    ]

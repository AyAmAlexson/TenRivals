from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0031_collection_groups'),
    ]

    operations = [
        migrations.CreateModel(
            name='OrderForMe',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order_number', models.CharField(db_index=True, max_length=20, unique=True)),
                ('first_name', models.CharField(max_length=120)),
                ('last_name', models.CharField(max_length=120)),
                ('email', models.EmailField(max_length=254)),
                ('phone', models.CharField(max_length=32)),
                ('telegram', models.CharField(blank=True, max_length=64)),
                ('contact_method', models.CharField(choices=[('EMAIL', 'Email'), ('WHATSAPP', 'WhatsApp'), ('TELEGRAM', 'Telegram')], db_index=True, default='EMAIL', max_length=16)),
                ('general_comment', models.TextField(blank=True)),
                ('estimated_total', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('status', models.CharField(choices=[('SUBMITTED', 'Submitted'), ('QUOTE_PROVIDED', 'Quote provided'), ('CANCELLED', 'Cancelled'), ('ORDERED', 'Ordered')], db_index=True, default='SUBMITTED', max_length=24)),
                ('status_changed_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='order_for_me_orders', to='shop.customer')),
            ],
            options={
                'ordering': ['-created_at', '-id'],
                'indexes': [models.Index(fields=['customer', '-created_at'], name='shop_orderf_custome_7b9ada_idx'), models.Index(fields=['status', '-created_at'], name='shop_orderf_status_a6c175_idx')],
            },
        ),
        migrations.CreateModel(
            name='OrderForMeYearSequence',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('year', models.PositiveIntegerField(db_index=True, unique=True)),
                ('last_seq', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Order For Me sequence (year)',
            },
        ),
        migrations.CreateModel(
            name='OrderForMeItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sort_order', models.PositiveSmallIntegerField(db_index=True, default=1)),
                ('item_url', models.URLField(max_length=1000)),
                ('item_comment', models.TextField(blank=True)),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='shop.orderforme')),
            ],
            options={
                'ordering': ['sort_order', 'id'],
            },
        ),
    ]

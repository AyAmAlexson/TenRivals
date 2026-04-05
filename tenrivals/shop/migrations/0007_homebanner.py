from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0006_product_prices_and_listing'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomeBanner',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('slot', models.CharField(choices=[('HERO_MAIN', 'Main hero (16:9)'), ('PROMO_LEFT', 'Promo left (2:1)'), ('PROMO_RIGHT', 'Promo right (2:1)')], db_index=True, max_length=16)),
                ('image', models.ImageField(upload_to='shop/home_banners/')),
                ('link_url', models.URLField(blank=True, max_length=500)),
                ('internal_note', models.CharField(blank=True, max_length=200)),
                ('archived_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]

# Home hero overlay: Russian and Georgian overrides (fallback to English)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0035_promo_codes_and_order_promo_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='homeherocontent',
            name='headline_ru',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Russian (ge_ru). Empty = use English fields above.',
            ),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='subtext_ru',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='cta_label_ru',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='cta_url_ru',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='secondary_link_label_ru',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='secondary_link_url_ru',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='headline_ka',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Georgian (ge_ka). Empty = use English fields above.',
            ),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='subtext_ka',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='cta_label_ka',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='cta_url_ka',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='secondary_link_label_ka',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='homeherocontent',
            name='secondary_link_url_ka',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]

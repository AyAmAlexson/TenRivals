from django.db import migrations, models

# Plain strings (migration snapshot); mirrors shop.site_locale.SITE_LOCALE_CHOICES_FULL labels.
_SITE_LOCALE_CHOICES = (
    ('ge_en', 'Georgia · English'),
    ('ge_ru', 'Georgia · Russian'),
    ('ge_ka', 'Georgia · Georgian'),
    ('ge_es', 'Georgia · Spanish'),
    ('ge_pt', 'Georgia · Portuguese'),
    ('ge_it', 'Georgia · Italian'),
    ('ge_fr', 'Georgia · French'),
    ('ge_de', 'Georgia · German'),
    ('ge_nl', 'Georgia · Dutch'),
    ('ge_pl', 'Georgia · Polish'),
    ('ge_uk', 'Georgia · Ukrainian'),
    ('ge_tr', 'Georgia · Turkish'),
)


class Migration(migrations.Migration):

    dependencies = [
        ('persons', '0011_customuser_preferred_site_locale'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customuser',
            name='preferred_site_locale',
            field=models.CharField(
                choices=_SITE_LOCALE_CHOICES,
                db_index=False,
                default='ge_en',
                help_text=(
                    'Preferred storefront language (country_language URL prefix); '
                    'only some values are routed yet.'
                ),
                max_length=8,
            ),
        ),
    ]

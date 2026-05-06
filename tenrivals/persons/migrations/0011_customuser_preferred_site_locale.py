from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('persons', '0010_remove_customuser_is_email_verified'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='preferred_site_locale',
            field=models.CharField(
                db_index=False,
                default='ge_en',
                help_text='Preferred storefront language (country_language URL prefix); only some values are routed yet.',
                max_length=8,
            ),
        ),
    ]

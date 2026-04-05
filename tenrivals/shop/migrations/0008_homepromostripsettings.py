from django.db import migrations, models


def create_default_promo_settings(apps, schema_editor):
    HomePromoStripSettings = apps.get_model('shop', 'HomePromoStripSettings')
    HomePromoStripSettings.objects.get_or_create(
        pk=1,
        defaults={'left_visible': True, 'right_visible': True},
    )


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0007_homebanner'),
    ]

    operations = [
        migrations.CreateModel(
            name='HomePromoStripSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('left_visible', models.BooleanField(default=True)),
                ('right_visible', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Home promo strip visibility',
                'verbose_name_plural': 'Home promo strip visibility',
            },
        ),
        migrations.RunPython(create_default_promo_settings, migrations.RunPython.noop),
    ]

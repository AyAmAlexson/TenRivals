from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0050_counter_store_choices'),
    ]

    operations = [
        migrations.CreateModel(
            name='StoredImageRendition',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_name', models.CharField(max_length=300, unique=True)),
                ('width', models.PositiveIntegerField(default=0)),
                ('height', models.PositiveIntegerField(default=0)),
                ('variants', models.JSONField(blank=True, default=dict)),
                ('version', models.PositiveSmallIntegerField(default=1)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Stored image rendition',
                'verbose_name_plural': 'Stored image renditions',
            },
        ),
    ]

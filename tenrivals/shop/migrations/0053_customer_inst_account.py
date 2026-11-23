from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0052_monthledger'),
    ]

    operations = [
        migrations.AddField(
            model_name='customer',
            name='inst_account',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Instagram username or handle (optional).',
                max_length=64,
            ),
        ),
    ]

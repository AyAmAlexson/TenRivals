from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('shop', '0045_customer_source_and_comment'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffAiInsight',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    choices=[('analytics', 'Sales analytics'), ('stock', 'Stock stats')],
                    db_index=True,
                    max_length=32,
                    unique=True,
                )),
                ('report', models.JSONField(blank=True, default=dict)),
                ('prompt_version', models.CharField(blank=True, default='', max_length=64)),
                ('model_version', models.CharField(blank=True, default='', max_length=64)),
                ('generated_at', models.DateTimeField(auto_now=True)),
                ('generated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='staff_ai_insights',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Staff AI insight',
                'verbose_name_plural': 'Staff AI insights',
            },
        ),
    ]

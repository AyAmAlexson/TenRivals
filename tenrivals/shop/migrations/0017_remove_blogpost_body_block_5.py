from django.db import migrations, models


def merge_fifth_block_into_fourth(apps, schema_editor):
    BlogPost = apps.get_model('shop', 'BlogPost')
    for row in BlogPost.objects.all():
        b4 = (row.body_block_4 or '').strip()
        b5 = (row.body_block_5 or '').strip()
        update_fields = ['body']
        if b5:
            row.body_block_4 = f'{b4}\n\n{b5}'.strip() if b4 else b5
            update_fields.insert(0, 'body_block_4')
        parts = [
            (row.body_block_1 or '').strip(),
            (row.body_block_2 or '').strip(),
            (row.body_block_3 or '').strip(),
            (row.body_block_4 or '').strip(),
        ]
        row.body = '\n\n'.join(p for p in parts if p)
        row.save(update_fields=update_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0016_blog_text_blocks_5'),
    ]

    operations = [
        migrations.RunPython(merge_fifth_block_into_fourth, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='blogpost',
            name='body_block_5',
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='body_block_1',
            field=models.TextField(blank=True, help_text='Article text block 1/4 (before quote).'),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='body_block_2',
            field=models.TextField(blank=True, help_text='Article text block 2/4 (after quote, before mid CTA).'),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='body_block_3',
            field=models.TextField(blank=True, help_text='Article text block 3/4 (after mid CTA, before product rail).'),
        ),
        migrations.AlterField(
            model_name='blogpost',
            name='body_block_4',
            field=models.TextField(blank=True, help_text='Article text block 4/4 (after product rail, before end CTA).'),
        ),
    ]

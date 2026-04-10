from django.db import migrations, models


def _forward_db(apps, schema_editor):
    Product = apps.get_model('shop', 'Product')
    Shoe = apps.get_model('shop', 'Shoe')

    color_on_product = models.CharField(max_length=80, blank=True, null=True)
    color_on_product.set_attributes_from_name('color')
    schema_editor.add_field(Product, color_on_product)

    connection = schema_editor.connection
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(
                """
                UPDATE shop_product AS p
                SET color = s.color
                FROM shop_shoe AS s
                WHERE s.product_ptr_id = p.id
                  AND s.color IS NOT NULL
                  AND TRIM(s.color) != ''
                """
            )
        else:
            cursor.execute(
                """
                UPDATE shop_product
                SET color = (
                    SELECT s.color FROM shop_shoe s
                    WHERE s.product_ptr_id = shop_product.id
                    LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM shop_shoe s2
                    WHERE s2.product_ptr_id = shop_product.id
                      AND s2.color IS NOT NULL
                      AND TRIM(s2.color) != ''
                )
                """
            )

    old_shoe_color = models.CharField(max_length=80, blank=True, null=True)
    old_shoe_color.set_attributes_from_name('color')
    old_shoe_color.model = Shoe
    schema_editor.remove_field(Shoe, old_shoe_color)


def _backward_db(apps, schema_editor):
    Product = apps.get_model('shop', 'Product')
    Shoe = apps.get_model('shop', 'Shoe')

    shoe_color = models.CharField(max_length=80, blank=True, null=True)
    shoe_color.set_attributes_from_name('color')
    schema_editor.add_field(Shoe, shoe_color)

    connection = schema_editor.connection
    with connection.cursor() as cursor:
        if connection.vendor == 'postgresql':
            cursor.execute(
                """
                UPDATE shop_shoe AS s
                SET color = p.color
                FROM shop_product AS p
                WHERE s.product_ptr_id = p.id
                  AND p.color IS NOT NULL
                  AND TRIM(p.color) != ''
                """
            )
        else:
            cursor.execute(
                """
                UPDATE shop_shoe
                SET color = (
                    SELECT p.color FROM shop_product p
                    WHERE p.id = shop_shoe.product_ptr_id
                    LIMIT 1
                )
                WHERE EXISTS (
                    SELECT 1 FROM shop_product p2
                    WHERE p2.id = shop_shoe.product_ptr_id
                      AND p2.color IS NOT NULL
                      AND TRIM(p2.color) != ''
                )
                """
            )

    product_color = models.CharField(max_length=80, blank=True, null=True)
    product_color.set_attributes_from_name('color')
    schema_editor.remove_field(Product, product_color)


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0021_salesorderline_variant_label'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.RemoveField(model_name='shoe', name='color'),
                migrations.AddField(
                    model_name='product',
                    name='color',
                    field=models.CharField(blank=True, max_length=80, null=True),
                ),
            ],
            database_operations=[
                migrations.RunPython(_forward_db, _backward_db),
            ],
        ),
    ]

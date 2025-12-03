# Generated migration for bonus field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('shop', '0002_product_credit'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='bonus',
            field=models.DecimalField(decimal_places=2, default=0.0, max_digits=10, verbose_name='Цена Bonus (скидка)'),
        ),
    ]

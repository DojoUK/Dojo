import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('organisations', '0001_initial'),
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='Product',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('category', models.CharField(choices=[('gi', 'Gi / Uniform'), ('belt', 'Belt'), ('protective', 'Protective gear'), ('apparel', 'Apparel'), ('accessory', 'Accessory'), ('other', 'Other')], default='other', max_length=20)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True, help_text='Inactive products are hidden from invoicing but keep their history')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('organisation', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='products', to='organisations.organisation')),
            ],
            options={
                'ordering': ['organisation', 'name'],
            },
        ),
        migrations.CreateModel(
            name='ProductVariant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('size', models.CharField(help_text='e.g. 000, 0, 1, 2, 3, 4, 5, XS, S, M, L, XL, Child 6, Adult L', max_length=30)),
                ('sku', models.CharField(blank=True, max_length=64)),
                ('price', models.DecimalField(decimal_places=2, max_digits=8)),
                ('quantity_in_stock', models.PositiveIntegerField(default=0)),
                ('low_stock_threshold', models.PositiveIntegerField(default=0, help_text='Show a low-stock warning at or below this level (0 = disabled)')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='variants', to='inventory.product')),
            ],
            options={
                'verbose_name': 'Product variant (size)',
                'ordering': ['product', 'size'],
                'unique_together': {('product', 'size')},
            },
        ),
        migrations.CreateModel(
            name='StockMovement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('quantity_change', models.IntegerField(help_text='Positive = stock added, negative = stock removed')),
                ('reason', models.CharField(choices=[('restock', 'Restock'), ('correction', 'Manual correction'), ('sale', 'Sale (invoice)'), ('return', 'Return / refund'), ('write_off', 'Write-off / damaged')], max_length=20)),
                ('notes', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('invoice', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='stock_movements', to='billing.invoice')),
                ('variant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='movements', to='inventory.productvariant')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]

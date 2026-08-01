import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
        ('billing', '0005_billingpolicy_additional_class_discount_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='InvoiceItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.CharField(blank=True, help_text='Snapshot of the product/size at time of sale', max_length=255)),
                ('quantity', models.PositiveIntegerField(default=1)),
                ('unit_price', models.DecimalField(decimal_places=2, help_text='Snapshot of the price at time of sale', max_digits=8)),
                ('invoice', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='billing.invoice')),
                ('variant', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='invoice_items', to='inventory.productvariant')),
            ],
            options={
                'ordering': ['pk'],
            },
        ),
    ]

from django.db import migrations


class Migration(migrations.Migration):
    """
    Merge the two billing leaves off 0006: the billing-policy/discount amount
    validators and the bank-connection/expense chain. The validator branch is
    Python-level validation only, so neither branch touches the other's tables.
    """

    dependencies = [
        ('billing', '0007_alter_billingpolicy_amount_and_more'),
        ('billing', '0010_alter_expense_id'),
    ]

    operations = []

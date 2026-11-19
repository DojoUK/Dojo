from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0014_billingpolicy_booking_plan'),
        ('classes', '0011_sessionabsence'),
    ]

    operations = [
        migrations.AddField(
            model_name='sessionbooking',
            name='status',
            field=models.CharField(choices=[('confirmed', 'Confirmed'), ('pending', 'Awaiting payment')],
                                   default='confirmed', max_length=10),
        ),
        migrations.AddField(
            model_name='sessionbooking',
            name='invoice',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='session_bookings', to='billing.invoice'),
        ),
    ]

from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('pages', '0007_order_sender_identity'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='balance_payment',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Manually entered remaining balance payment',
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
        migrations.AddField(
            model_name='order',
            name='delivery_fee_charge',
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text='Manually entered delivery fee charge',
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(0)],
            ),
        ),
    ]

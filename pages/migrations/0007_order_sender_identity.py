from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('pages', '0006_order_delivery_time_order_rider_name_and_more')]
    operations = [
        migrations.AddField(model_name='order', name='delivery_address', field=models.TextField(blank=True, help_text='Drop-off address for this order')),
        migrations.AddField(model_name='order', name='sender_address', field=models.TextField(blank=True, help_text='Complete address of sender')),
        migrations.AddField(model_name='order', name='sender_is_receiver', field=models.BooleanField(default=False, help_text='Sender and receiver are the same person')),
    ]

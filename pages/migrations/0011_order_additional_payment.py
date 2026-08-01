from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pages', '0010_merge_0008_and_0009'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='additional_payment',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Manual additional payment note or amount, such as balance payment plus DF charge',
                max_length=255,
            ),
        ),
    ]

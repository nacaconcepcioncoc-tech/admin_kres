from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pages', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='fulfilled_by',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Who fulfilled the order (staff name)",
                max_length=100,
            ),
        ),
    ]

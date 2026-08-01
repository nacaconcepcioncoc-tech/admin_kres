from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('pages', '0018_employee_standing_pin'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='employeemonthlyperformance',
            name='has_lapse',
        ),
        migrations.AlterField(
            model_name='employeemonthlyperformance',
            name='demerits',
            field=models.PositiveSmallIntegerField(
                default=0,
                validators=[
                    django.core.validators.MinValueValidator(0),
                    django.core.validators.MaxValueValidator(999),
                ],
            ),
        ),
    ]

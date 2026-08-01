from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('pages', '0017_employee_monthly_performance'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeStandingPin',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pin_hash', models.CharField(max_length=128)),
                ('is_active', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('updated_by', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='+',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'pages_employee_standing_pin',
                'verbose_name': 'Employee Standing PIN',
                'verbose_name_plural': 'Employee Standing PIN',
            },
        ),
        migrations.AddConstraint(
            model_name='employeestandingpin',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_active', True)),
                fields=('is_active',),
                name='one_active_employee_standing_pin',
            ),
        ),
    ]

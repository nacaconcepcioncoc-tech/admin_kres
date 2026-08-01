from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('pages', '0012_employee_performance_records'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonthlyCleanupRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField(help_text='First day of the month that was cleaned', unique=True)),
                ('completed_at', models.DateTimeField(auto_now_add=True)),
                ('orders_deleted', models.PositiveIntegerField(default=0)),
                ('performance_records_deleted', models.PositiveIntegerField(default=0)),
            ],
            options={
                'db_table': 'pages_monthlycleanuprun',
                'ordering': ['-month'],
            },
        ),
    ]

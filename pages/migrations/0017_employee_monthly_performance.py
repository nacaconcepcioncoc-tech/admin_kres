from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('pages', '0016_order_receiver_name_and_sender_customer_data'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeMonthlyPerformance',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.PositiveSmallIntegerField(
                    choices=[(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8), (9, 9), (10, 10), (11, 11), (12, 12)],
                    validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(12)],
                )),
                ('year', models.PositiveSmallIntegerField(
                    validators=[django.core.validators.MinValueValidator(2000), django.core.validators.MaxValueValidator(2100)],
                )),
                ('stars', models.PositiveSmallIntegerField(
                    choices=[(0, '0'), (1, '1'), (2, '2')],
                    validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(2)],
                )),
                ('has_lapse', models.BooleanField(default=False)),
                ('demerits', models.PositiveSmallIntegerField(
                    default=0,
                    editable=False,
                    validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(1)],
                )),
                ('admin_remarks', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='monthly_evaluations',
                    to='pages.employee',
                )),
            ],
            options={
                'db_table': 'employee_monthly_performance',
                'ordering': ['-year', '-month', 'employee__full_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='employeemonthlyperformance',
            constraint=models.UniqueConstraint(
                fields=('employee', 'month', 'year'),
                name='unique_employee_month_year_evaluation',
            ),
        ),
        migrations.AddIndex(
            model_name='employeemonthlyperformance',
            index=models.Index(
                fields=['employee', 'year'],
                name='emp_monthly_perf_emp_year_idx',
            ),
        ),
    ]

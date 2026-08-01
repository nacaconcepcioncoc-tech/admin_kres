from uuid import uuid4

from django.db import migrations, models


def migrate_record_types(apps, schema_editor):
    PerformanceRecord = apps.get_model('pages', 'PerformanceRecord')
    PerformanceRecord.objects.filter(record_type='star').update(record_type='positive')
    PerformanceRecord.objects.filter(record_type='demerit').update(record_type='negative')


def seed_submission_keys(apps, schema_editor):
    PerformanceRecord = apps.get_model('pages', 'PerformanceRecord')
    for record in PerformanceRecord.objects.filter(submission_key__isnull=True).iterator():
        record.submission_key = uuid4()
        record.save(update_fields=['submission_key'])


class Migration(migrations.Migration):
    dependencies = [('pages', '0014_alter_order_customer_address_and_more')]

    operations = [
        migrations.AddField(
            model_name='employee',
            name='monthly_points',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='employee',
            name='performance_status',
            field=models.CharField(default='Not Yet Evaluated', max_length=40),
        ),
        migrations.AlterField(
            model_name='employee',
            name='total_stars',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='employee',
            name='total_demerits',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='performancerecord',
            name='record_type',
            field=models.CharField(choices=[('positive', 'Positive'), ('negative', 'Negative')], max_length=10),
        ),
        migrations.AddField(
            model_name='performancerecord',
            name='submission_key',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(migrate_record_types, migrations.RunPython.noop),
        migrations.RunPython(seed_submission_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='performancerecord',
            name='submission_key',
            field=models.UUIDField(editable=False, unique=True),
        ),
        migrations.CreateModel(
            name='MonthlyPerformanceSummary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('month', models.DateField(help_text='First day of the archived Manila month')),
                ('final_points', models.IntegerField(default=0)),
                ('stars', models.PositiveSmallIntegerField(default=0)),
                ('demerits', models.PositiveSmallIntegerField(default=0)),
                ('performance_status', models.CharField(max_length=40)),
                ('archived_at', models.DateTimeField(auto_now_add=True)),
                ('employee', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='monthly_performance_summaries', to='pages.employee')),
            ],
            options={
                'db_table': 'pages_monthlyperformancesummary',
                'ordering': ['-month', 'employee__full_name'],
            },
        ),
        migrations.AddConstraint(
            model_name='monthlyperformancesummary',
            constraint=models.UniqueConstraint(fields=('employee', 'month'), name='unique_employee_monthly_summary'),
        ),
    ]

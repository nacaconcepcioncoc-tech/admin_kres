from django.db import migrations, models


PAYMENT_TYPE_CHOICES = [
    ('full_payment', 'Full Payment'),
    ('down_payment', 'Down Payment'),
    ('balance_payment', 'Balance Payment'),
]


def ensure_payment_type_column(apps, schema_editor):
    """Bridge Django state to the nullable column already added in Phase 1."""
    payment_model = apps.get_model('pages', 'Payment')
    table_name = payment_model._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor,
                table_name,
            )
        }
    if 'payment_type' in existing_columns:
        return

    field = models.CharField(
        max_length=20,
        choices=PAYMENT_TYPE_CHOICES,
        null=True,
        blank=True,
    )
    field.set_attributes_from_name('payment_type')
    field.model = payment_model
    schema_editor.add_field(payment_model, field)


class Migration(migrations.Migration):
    dependencies = [
        ('pages', '0019_manual_employee_demerits'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_payment_type_column, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='payment',
                    name='payment_status',
                    field=models.CharField(
                        max_length=20,
                        default='pending',
                        choices=[
                            ('down_payment', 'Down Payment'),
                            ('fully_paid', 'Fully Paid'),
                            ('pending', 'Pending'),
                            ('completed', 'Completed'),
                            ('failed', 'Failed'),
                            ('refunded', 'Refunded'),
                        ],
                    ),
                ),
                migrations.AddField(
                    model_name='payment',
                    name='payment_type',
                    field=models.CharField(
                        max_length=20,
                        choices=PAYMENT_TYPE_CHOICES,
                        null=True,
                        blank=True,
                        help_text='NULL identifies an unreconciled legacy payment.',
                    ),
                ),
            ],
        ),
    ]

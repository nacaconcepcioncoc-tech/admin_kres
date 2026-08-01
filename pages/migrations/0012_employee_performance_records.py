from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


def prepare_employee_tables(apps, schema_editor):
    """
    Reuse the existing Supabase pages_employee table, add only missing
    performance columns, and create the performance-record table.
    """
    connection = schema_editor.connection
    existing_tables = set(connection.introspection.table_names())

    Employee = apps.get_model("pages", "Employee")
    PerformanceRecord = apps.get_model("pages", "PerformanceRecord")

    if Employee._meta.db_table not in existing_tables:
        schema_editor.create_model(Employee)
    else:
        with connection.cursor() as cursor:
            description = connection.introspection.get_table_description(
                cursor, Employee._meta.db_table
            )
        existing_columns = {column.name for column in description}

        fields_to_add = (
            "total_stars",
            "total_demerits",
            "overall_rating",
            "created_at",
            "updated_at",
        )

        for field_name in fields_to_add:
            field = Employee._meta.get_field(field_name)

            if field.column in existing_columns:
                continue

            # Existing employee rows require a value when adding non-null
            # date/time columns. The temporary default is used only while
            # the column is being added.
            original_default = field.default
            if field_name in {"created_at", "updated_at"}:
                field.default = timezone.now

            schema_editor.add_field(Employee, field)
            field.default = original_default

    existing_tables = set(connection.introspection.table_names())
    if PerformanceRecord._meta.db_table not in existing_tables:
        schema_editor.create_model(PerformanceRecord)


class Migration(migrations.Migration):
    dependencies = [
        ("pages", "0011_order_additional_payment"),
    ]

    operations = [
        # First put the models into Django's migration state. No database
        # table is created by this operation.
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Employee",
                    fields=[
                        (
                            "id",
                            models.BigAutoField(
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("full_name", models.CharField(max_length=150)),
                        ("position", models.CharField(max_length=120)),
                        (
                            "profile_picture_url",
                            models.URLField(blank=True, null=True),
                        ),
                        (
                            "total_stars",
                            models.PositiveIntegerField(default=0),
                        ),
                        (
                            "total_demerits",
                            models.PositiveIntegerField(default=0),
                        ),
                        (
                            "overall_rating",
                            models.DecimalField(
                                decimal_places=2,
                                default=Decimal("3.00"),
                                max_digits=4,
                            ),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        ("updated_at", models.DateTimeField(auto_now=True)),
                    ],
                    options={
                        "db_table": "pages_employee",
                        "ordering": ["full_name"],
                    },
                ),
                migrations.CreateModel(
                    name="PerformanceRecord",
                    fields=[
                        (
                            "record_id",
                            models.BigAutoField(
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        (
                            "record_type",
                            models.CharField(
                                choices=[
                                    ("star", "Star"),
                                    ("demerit", "Demerit"),
                                ],
                                max_length=10,
                            ),
                        ),
                        ("description", models.TextField()),
                        (
                            "points",
                            models.PositiveIntegerField(
                                validators=[
                                    MinValueValidator(1),
                                    MaxValueValidator(100),
                                ]
                            ),
                        ),
                        (
                            "record_date",
                            models.DateField(default=timezone.localdate),
                        ),
                        ("created_at", models.DateTimeField(auto_now_add=True)),
                        (
                            "employee",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="performance_records",
                                to="pages.employee",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "pages_performancerecord",
                        "ordering": ["-record_date", "-created_at"],
                    },
                ),
            ],
            database_operations=[],
        ),

        # This runs after Employee and PerformanceRecord already exist in
        # Django's migration state, so apps.get_model() works correctly.
        migrations.RunPython(
            prepare_employee_tables,
            migrations.RunPython.noop,
        ),
    ]

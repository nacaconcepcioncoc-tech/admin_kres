from django.db import migrations


OBSOLETE_ORDER_FIELDS = (
    "sender_address",
    "rider_phone",
    "rider_vehicle",
    "tax",
    "discount",
)


def remove_existing_order_columns(apps, schema_editor):
    """Drop only columns that exist, using the active database backend."""
    order_model = apps.get_model("pages", "Order")
    table_name = order_model._meta.db_table

    with schema_editor.connection.cursor() as cursor:
        existing_columns = {
            column.name
            for column in schema_editor.connection.introspection.get_table_description(
                cursor, table_name
            )
        }

    for field_name in OBSOLETE_ORDER_FIELDS:
        field = order_model._meta.get_field(field_name)
        if field.column not in existing_columns:
            continue
        schema_editor.remove_field(order_model, field)
        existing_columns.remove(field.column)


class Migration(migrations.Migration):
    dependencies = [("pages", "0020_payment_payment_type")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(
                    remove_existing_order_columns,
                    reverse_code=migrations.RunPython.noop,
                ),
            ],
            state_operations=[
                migrations.RemoveField(model_name="order", name="sender_address"),
                migrations.RemoveField(model_name="order", name="rider_phone"),
                migrations.RemoveField(model_name="order", name="rider_vehicle"),
                migrations.RemoveField(model_name="order", name="tax"),
                migrations.RemoveField(model_name="order", name="discount"),
            ],
        ),
    ]

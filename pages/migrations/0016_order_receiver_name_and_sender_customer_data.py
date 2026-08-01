from django.db import migrations, models


def preserve_receiver_and_link_sender_customer(apps, schema_editor):
    Order = apps.get_model("pages", "Order")
    Customer = apps.get_model("pages", "Customer")

    for order in Order.objects.select_related("customer").all().iterator():
        old_customer = order.customer
        old_receiver_name = " ".join(
            part for part in [old_customer.first_name, old_customer.last_name] if part
        ).strip()

        # Preserve the receiver before repointing the Customer foreign key.
        if not order.receiver_name:
            order.receiver_name = old_receiver_name

        sender_name = (order.sender_name or "").strip()
        sender_phone = (order.sender_phone or "").strip()

        # Only migrate records where sender identity is reliably available.
        if sender_name and sender_phone:
            parts = sender_name.split()
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
            safe_phone = "".join(ch for ch in sender_phone if ch.isdigit()) or str(order.order_id)
            email = f"sender_{safe_phone}_{order.order_id}@kres.local"

            sender_customer, _ = Customer.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "phone": sender_phone,
                    "address": order.sender_address or "",
                },
            )
            order.customer = sender_customer

        order.save(update_fields=["receiver_name", "customer"])


def reverse_noop(apps, schema_editor):
    # The original receiver Customer link cannot be reconstructed safely.
    pass


class Migration(migrations.Migration):
    dependencies = [("pages", "0015_monthly_points_performance")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="receiver_name",
            field=models.CharField(blank=True, help_text="Name of the delivery receiver", max_length=200),
        ),
        migrations.RunPython(preserve_receiver_and_link_sender_customer, reverse_noop),
    ]

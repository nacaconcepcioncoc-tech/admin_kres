from decimal import Decimal


STATE_DOWN_PAYMENT = "down_payment"
STATE_FULLY_PAID = "fully_paid"
STATE_UNRECONCILED = "unreconciled"

STATE_LABELS = {
    STATE_DOWN_PAYMENT: "Down Payment",
    STATE_FULLY_PAID: "Fully Paid",
    STATE_UNRECONCILED: "Unreconciled",
}


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def calculate_order_payment_state(order, payments):
    """Return the customer payment state from the typed payment ledger only."""
    payment_rows = list(payments)
    order_total = _money(order.total)

    if not payment_rows or any(row.payment_type is None for row in payment_rows):
        return {
            "code": STATE_UNRECONCILED,
            "label": STATE_LABELS[STATE_UNRECONCILED],
            "remaining_balance": _money(order.balance_payment),
            "is_legacy": True,
        }

    received_rows = [
        row for row in payment_rows
        if row.payment_status not in ("failed", "refunded")
    ]
    received_total = sum((_money(row.amount) for row in received_rows), Decimal("0.00"))
    remaining_balance = max(order_total - received_total, Decimal("0.00")).quantize(
        Decimal("0.01")
    )
    payment_types = [row.payment_type for row in received_rows]

    is_full_payment = (
        payment_types == ["full_payment"]
        and remaining_balance == Decimal("0.00")
    )
    is_open_down_payment = (
        payment_types == ["down_payment"]
        and remaining_balance > Decimal("0.00")
    )
    is_settled_down_payment = (
        len(payment_types) == 2
        and payment_types.count("down_payment") == 1
        and payment_types.count("balance_payment") == 1
        and remaining_balance == Decimal("0.00")
    )

    if is_full_payment or is_settled_down_payment:
        code = STATE_FULLY_PAID
    elif is_open_down_payment:
        code = STATE_DOWN_PAYMENT
    else:
        code = STATE_UNRECONCILED

    return {
        "code": code,
        "label": STATE_LABELS[code],
        "remaining_balance": remaining_balance,
        "is_legacy": code == STATE_UNRECONCILED,
    }


def calculate_order_payment_display_state(order, payments):
    """Return the two-state employee display without weakening core protection."""
    state = calculate_order_payment_state(order, payments)
    if state["code"] != STATE_UNRECONCILED:
        return state

    display_code = (
        STATE_DOWN_PAYMENT
        if state["remaining_balance"] > Decimal("0.00")
        else STATE_FULLY_PAID
    )
    return {
        **state,
        "code": display_code,
        "label": STATE_LABELS[display_code],
        "internal_code": STATE_UNRECONCILED,
    }

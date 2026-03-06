// orders-ajax.js — KRES Admin Orders Integration

// ── CSRF helper ───────────────────────────────────────────────────────────────
function getCookie(name) {
    let value = null;
    if (document.cookie && document.cookie !== '') {
        for (const cookie of document.cookie.split(';')) {
            const c = cookie.trim();
            if (c.startsWith(name + '=')) {
                value = decodeURIComponent(c.slice(name.length + 1));
                break;
            }
        }
    }
    return value;
}

// ── Modal open / close ────────────────────────────────────────────────────────
function openAddOrderModal() {
    document.getElementById('addOrderModal').classList.add('show');
    clearFormError();
}

function closeAddOrderModal() {
    document.getElementById('addOrderModal').classList.remove('show');
    document.getElementById('addOrderForm').reset();
    // Reset payment chips back to Pending
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('selected'));
    const pendingChip = document.querySelector('.chip[data-value="pending"]');
    if (pendingChip) pendingChip.classList.add('selected');
    const statusInput = document.getElementById('paymentStatus');
    if (statusInput) statusInput.value = 'pending';
    clearFormError();
}

// Alias so both old and new close buttons work
function closeModal() { closeAddOrderModal(); }

// ── Chip selector ─────────────────────────────────────────────────────────────
function selectChip(el) {
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
    document.getElementById('paymentStatus').value = el.getAttribute('data-value');
}

// ── Inline error banner ───────────────────────────────────────────────────────
function showFormError(msg) {
    const banner = document.getElementById('formErrorBanner');
    const text   = document.getElementById('errorText');
    if (banner && text) {
        text.textContent = msg;
        banner.classList.add('show');
        banner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
}

function clearFormError() {
    const banner = document.getElementById('formErrorBanner');
    if (banner) banner.classList.remove('show');
}

// ── MAIN: Create Order ────────────────────────────────────────────────────────
function submitAddOrder(event) {
    event.preventDefault();
    clearFormError();

    const customerName    = document.getElementById('customerName')?.value?.trim();
    const contactNumber   = document.getElementById('contactNumber')?.value?.trim();
    const address         = document.getElementById('address')?.value?.trim();
    const deliveryDate    = document.getElementById('deliveryDate')?.value?.trim();
    const orderProduct    = document.getElementById('orderProduct')?.value?.trim();
    const specialRequests = document.getElementById('specialRequests')?.value?.trim() || '';
    const paymentMethod   = document.getElementById('paymentMethod')?.value;
    const paymentStatus   = document.getElementById('paymentStatus')?.value;

    if (!customerName)   { showFormError('Please enter the customer name.'); return; }
    if (!contactNumber)  { showFormError('Please enter a contact number.'); return; }
    if (!address)        { showFormError('Please enter a delivery address.'); return; }
    if (!orderProduct)   { showFormError('Please enter the product order.'); return; }
    if (!deliveryDate)   { showFormError('Please select a delivery date.'); return; }
    if (!paymentMethod)  { showFormError('Please select a payment method.'); return; }
    if (!paymentStatus)  { showFormError('Please select a payment status.'); return; }

    const nameParts = customerName.split(/\s+/);
    const firstName = nameParts[0];
    const lastName  = nameParts.length > 1 ? nameParts.slice(1).join(' ') : '';
    const autoEmail = 'customer_' + contactNumber.replace(/\D/g, '') + '_' + Date.now() + '@kres.local';

    const payload = {
        customer_email:      autoEmail,
        customer_first_name: firstName,
        customer_last_name:  lastName,
        customer_phone:      contactNumber,
        customer_address:    address,
        items: [{ product_name: orderProduct, quantity: 1, unit_price: 0 }],
        notes:          specialRequests,
        payment_method: paymentMethod,
        payment_status: paymentStatus,
        delivery_date:  deliveryDate,
        tax: 0, discount: 0
    };

    const btn = event.target.querySelector('button[type="submit"]') ||
                document.querySelector('button[form="addOrderForm"]');
    const origHTML = btn.innerHTML;
    btn.innerHTML = '⏳ Creating...';
    btn.disabled  = true;

    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value || getCookie('csrftoken') || '';

    fetch('/ajax/order/create/', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
        body:    JSON.stringify(payload)
    })
    .then(r => { if (!r.ok) throw new Error('Server error ' + r.status); return r.json(); })
    .then(data => {
        btn.innerHTML = origHTML;
        btn.disabled  = false;
        if (data.success) {
            btn.innerHTML = '✓ Created!';
            btn.style.background = 'linear-gradient(135deg, #059669, #10b981)';
            setTimeout(() => location.reload(), 1200);
        } else {
            showFormError(data.message || 'Failed to create order. Please try again.');
        }
    })
    .catch(err => {
        btn.innerHTML = origHTML;
        btn.disabled  = false;
        showFormError('Could not reach the server: ' + err.message);
    });
}

// ── View Order Details ────────────────────────────────────────────────────────
function viewOrderDetails(orderId) {
    const row = document.querySelector('tr[data-id="' + orderId + '"]');
    if (row) {
        document.getElementById('detailOrderId').textContent         = row.querySelector('.order-id')?.textContent?.trim()      || orderId;
        document.getElementById('detailCustomerName').textContent    = row.querySelector('.customer-name')?.textContent?.trim() || '-';
        document.getElementById('detailFacebookAccount').textContent = row.getAttribute('data-facebook') || '-';
        document.getElementById('detailContactNumber').textContent   = row.getAttribute('data-phone')    || '-';
        document.getElementById('detailAddress').textContent         = row.getAttribute('data-address')  || '-';
        document.getElementById('detailOrderItem').textContent       = row.querySelector('.item-name')?.textContent?.trim()     || '-';
        document.getElementById('detailOrderAmount').textContent     = row.querySelector('.order-amount')?.textContent?.trim()  || '-';
        document.getElementById('detailDeliveryDate').textContent    = row.querySelector('.order-date')?.textContent?.trim()    || '-';
        document.getElementById('detailStatus').textContent          = row.querySelector('.status-badge')?.textContent?.trim()  || '-';
        document.getElementById('detailSpecialRequests').textContent = row.getAttribute('data-notes') || 'None';
    }
    document.getElementById('orderDetailsModal')?.classList.add('show');
}

function closeOrderDetailsModal() {
    document.getElementById('orderDetailsModal')?.classList.remove('show');
}

// ── Notifications ─────────────────────────────────────────────────────────────
function toggleNotifications(event) {
    event.stopPropagation();
    document.getElementById('notificationDropdown').classList.toggle('show');
}

document.addEventListener('click', function(e) {
    const icon = document.querySelector('.notification-icon');
    if (icon && !icon.contains(e.target)) {
        document.getElementById('notificationDropdown')?.classList.remove('show');
    }
});

// ── Status filter ─────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function() {
    const statusFilter = document.getElementById('statusFilter');
    if (statusFilter) {
        statusFilter.addEventListener('change', function() {
            const selected = this.value.toLowerCase();
            document.querySelectorAll('.orders-table tbody tr').forEach(row => {
                const rowStatus = row.getAttribute('data-status');
                row.style.display = (selected === 'all' || rowStatus === selected) ? '' : 'none';
            });
        });
    }

    const filter = sessionStorage.getItem('orderFilter');
    if (filter) {
        document.querySelectorAll('.orders-table tbody tr').forEach(row => {
            const badge  = row.querySelector('.status-badge');
            const status = badge ? badge.textContent.trim().toLowerCase() : '';
            row.style.display = (status === filter) ? '' : 'none';
        });
        window.addEventListener('beforeunload', () => sessionStorage.removeItem('orderFilter'));
    }
});

// ── Close modals on outside click ────────────────────────────────────────────
window.addEventListener('click', function(e) {
    if (e.target === document.getElementById('addOrderModal'))     closeAddOrderModal();
    if (e.target === document.getElementById('orderDetailsModal')) closeOrderDetailsModal();
});

console.log('Orders AJAX integration loaded successfully!');
// Orders Page Functionality

document.addEventListener('DOMContentLoaded', function() {
    initializeNavigation();
    initializeStatusFilter();
    initializeTableActions();
    initializeChatIcon();
    initializeAddNewOrder();
    initializeLogout();
});

// Navigation functionality
function initializeNavigation() {
    const navItems = document.querySelectorAll('.nav-item');
    
    navItems.forEach(item => {
        item.addEventListener('click', function(e) {
            e.preventDefault();
            
            // Remove active class from all items
            navItems.forEach(nav => nav.classList.remove('active'));
            
            // Add active class to clicked item
            this.classList.add('active');
            
            // Log navigation
            const pageName = this.querySelector('span').textContent;
            console.log(`Navigating to: ${pageName}`);
        });
    });
}

// Status filter functionality
function initializeStatusFilter() {
    const statusFilter = document.getElementById('statusFilter');
    const tableRows = document.querySelectorAll('.orders-table tbody tr');
    
    if (statusFilter) {
        statusFilter.addEventListener('change', function() {
            const selectedStatus = this.value.toLowerCase();
            
            tableRows.forEach(row => {
                const rowStatus = row.getAttribute('data-status');
                
                if (selectedStatus === 'all' || rowStatus === selectedStatus) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
            
            console.log(`Filtering by status: ${selectedStatus}`);
        });
    }
}

// Table actions - View Details
function initializeTableActions() {
    const viewDetailsButtons = document.querySelectorAll('.btn-view-details');
    
    viewDetailsButtons.forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const row = this.closest('tr');
            
            // Get order details
            const orderId = row.querySelector('.order-id').textContent;
            const customerName = row.querySelector('.customer-name').textContent;
            const itemName = row.querySelector('.item-name').textContent;
            const orderDate = row.querySelector('.order-date').textContent;
            const orderAmount = row.querySelector('.order-amount').textContent;
            const status = row.querySelector('.status-badge').textContent;
            
            // Show order details
            showOrderDetails({
                orderId,
                customerName,
                itemName,
                orderDate,
                orderAmount,
                status
            });
        });
    });
    
    // Add row click functionality (optional)
    const tableRows = document.querySelectorAll('.orders-table tbody tr');
    tableRows.forEach(row => {
        row.style.cursor = 'pointer';
        
        row.addEventListener('click', function(e) {
            // Don't trigger if clicking the View Details button
            if (e.target.classList.contains('btn-view-details')) {
                return;
            }
            
            // Highlight selected row
            tableRows.forEach(r => r.style.backgroundColor = '');
            this.style.backgroundColor = '#f0f9ff';
            
            const orderId = this.querySelector('.order-id').textContent;
            console.log(`Selected order: ${orderId}`);
        });
    });
}

// Show order details modal/alert
function showOrderDetails(order) {
    console.log('Order Details:', order);
    
    // Create a formatted message
    const message = `
ORDER DETAILS
━━━━━━━━━━━━━━━━━━━━━━━━━━

Order ID: ${order.orderId}
Customer: ${order.customerName}
Item: ${order.itemName}
Date: ${order.orderDate}
Amount: ${order.orderAmount}
Status: ${order.status}

━━━━━━━━━━━━━━━━━━━━━━━━━━

Actions available:
• Update Status
• Edit Order
• Print Invoice
• Contact Customer
    `;
    
    alert(message);
    
    // In a real application, you would open a modal instead
    // For example: openOrderDetailsModal(order);
}

// Chat icon functionality
function initializeChatIcon() {
    const chatIcon = document.querySelector('.chat-icon');
    
    if (chatIcon) {
        chatIcon.addEventListener('click', function() {
            console.log('Chat icon clicked');
            alert('Opening chat/messages...\n\nYou have 1 new message.');
        });
    }
}

// Add new order functionality
function initializeAddNewOrder() {
    const addOrderBtn = document.querySelector('.btn-add-order');
    
    if (addOrderBtn) {
        addOrderBtn.addEventListener('click', function() {
            console.log('Add new order clicked');
            showAddOrderForm();
        });
    }
}

// Show add order form
function showAddOrderForm() {
    const message = `
ADD NEW ORDER
━━━━━━━━━━━━━━━━━━━━━━━━━━

Enter order details:
• Customer Information
• Select Items
• Delivery Date
• Payment Method
• Special Instructions

━━━━━━━━━━━━━━━━━━━━━━━━━━

(Order form would open here)
    `;
    
    alert(message);
    
    // In a real application, you would open a form modal
    // For example: openAddOrderModal();
}

// Logout functionality
function initializeLogout() {
    const logoutIcon = document.querySelector('.logout-icon');
    
    if (logoutIcon) {
        logoutIcon.addEventListener('click', function(e) {
            e.stopPropagation();
            
            if (confirm('Are you sure you want to logout?')) {
                console.log('Logging out...');
                alert('Logout functionality would be implemented here');
                // In a real app: window.location.href = '/logout';
            }
        });
    }
}

// Utility function: Format currency
function formatCurrency(amount) {
    return `₱ ${amount.toLocaleString()}`;
}

// Utility function: Format date
function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString('en-US', {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit'
    });
}

// Utility function: Get order statistics
function getOrderStatistics() {
    const tableRows = document.querySelectorAll('.orders-table tbody tr');
    const stats = {
        total: tableRows.length,
        pending: 0,
        processing: 0,
        completed: 0
    };
    
    tableRows.forEach(row => {
        const status = row.getAttribute('data-status');
        if (status === 'pending') stats.pending++;
        else if (status === 'processing') stats.processing++;
        else if (status === 'completed') stats.completed++;
    });
    
    return stats;
}

// Utility function: Calculate total revenue
function calculateTotalRevenue() {
    const tableRows = document.querySelectorAll('.orders-table tbody tr');
    let total = 0;
    
    tableRows.forEach(row => {
        const amountText = row.querySelector('.order-amount').textContent;
        // Extract number from "₱ 1,300" format
        const amount = parseInt(amountText.replace(/[^\d]/g, ''));
        total += amount;
    });
    
    return total;
}

// Log initial statistics
const stats = getOrderStatistics();
const revenue = calculateTotalRevenue();

console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log('ORDERS STATISTICS');
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━');
console.log(`Total Orders: ${stats.total}`);
console.log(`Pending: ${stats.pending}`);
console.log(`Processing: ${stats.processing}`);
console.log(`Completed: ${stats.completed}`);
console.log(`Total Revenue: ₱ ${revenue.toLocaleString()}`);
console.log('━━━━━━━━━━━━━━━━━━━━━━━━━━');

// Add smooth scroll behavior
document.documentElement.style.scrollBehavior = 'smooth';

console.log('KRES Admin Orders Page initialized successfully!');
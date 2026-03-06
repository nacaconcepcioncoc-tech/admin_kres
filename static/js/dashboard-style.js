// Dashboard Functionality

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    initializeNavigation();
    initializeNotifications();
    initializeStats();
    initializeTableInteractions();
    initializeUserProfile();
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
            
            // Log navigation (you can extend this to actually navigate)
            const pageName = this.querySelector('span').textContent;
            console.log(`Navigating to: ${pageName}`);
            
            // You can add actual page navigation logic here
            // For example: window.location.href = `/${pageName.toLowerCase()}.html`;
        });
    });
}

// Notification functionality
function initializeNotifications() {
    const notificationIcon = document.querySelector('.notification-icon');
    const notificationBadge = document.querySelector('.notification-badge');
    
    if (notificationIcon) {
        notificationIcon.addEventListener('click', function() {
            // Toggle notification panel (you can implement a dropdown here)
            console.log('Notifications clicked');
            
            // Example: Show alert with notification count
            const count = notificationBadge.textContent;
            alert(`You have ${count} new notifications`);
            
            // You can implement a proper notification dropdown here
        });
    }
}

// Stats card animations and interactions
function initializeStats() {
    const statCards = document.querySelectorAll('.stat-card');
    
    statCards.forEach(card => {
        card.addEventListener('click', function() {
            const label = this.querySelector('.stat-label').textContent;
            const value = this.querySelector('.stat-value').textContent;
            
            console.log(`${label}: ${value}`);
            
            // You can add modal or detail view here
        });
    });
    
    // Animate numbers on load
    animateStats();
}

// Animate stat numbers
function animateStats() {
    const statValues = document.querySelectorAll('.stat-value');
    
    statValues.forEach(stat => {
        const text = stat.textContent.trim();
        
        // Check if it's a number (extract numeric part)
        const numericMatch = text.match(/[\d,]+/);
        
        if (numericMatch) {
            const targetNumber = parseInt(numericMatch[0].replace(/,/g, ''));
            const prefix = text.split(numericMatch[0])[0];
            const suffix = text.split(numericMatch[0])[1] || '';
            
            animateNumber(stat, 0, targetNumber, 1000, prefix, suffix);
        }
    });
}

// Animate number counting
function animateNumber(element, start, end, duration, prefix = '', suffix = '') {
    const startTime = performance.now();
    
    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        // Easing function
        const easeOutQuart = 1 - Math.pow(1 - progress, 4);
        const current = Math.floor(start + (end - start) * easeOutQuart);
        
        // Format number with commas
        const formattedNumber = current.toLocaleString();
        element.textContent = `${prefix}${formattedNumber}${suffix}`;
        
        if (progress < 1) {
            requestAnimationFrame(update);
        }
    }
    
    requestAnimationFrame(update);
}

// Table interactions
function initializeTableInteractions() {
    const tableRows = document.querySelectorAll('.orders-table tbody tr');
    
    tableRows.forEach(row => {
        row.addEventListener('click', function() {
            const orderId = this.cells[0].textContent;
            const customer = this.cells[1].textContent;
            const status = this.querySelector('.status-badge').textContent;
            
            console.log(`Order ${orderId} - Customer: ${customer} - Status: ${status}`);
            
            // You can add modal or detail view here
            showOrderDetails(orderId, customer, status);
        });
        
        // Add hover effect
        row.style.cursor = 'pointer';
    });
}

// Show order details (example function)
function showOrderDetails(orderId, customer, status) {
    // This is a placeholder - you can implement a modal here
    console.log('Order Details:', { orderId, customer, status });
    
    // Example alert (replace with proper modal)
    alert(`Order: ${orderId}
Customer: ${customer}
Status: ${status}

Click to view full details...`);
}

// User profile interactions
function initializeUserProfile() {
    const logoutIcon = document.querySelector('.logout-icon');
    const userProfile = document.querySelector('.user-profile');
    
    if (logoutIcon) {
        logoutIcon.addEventListener('click', function(e) {
            e.stopPropagation();
            
            const confirmLogout = confirm('Are you sure you want to logout?');
            
            if (confirmLogout) {
                console.log('Logging out...');
                // Add logout logic here
                // For example: window.location.href = '/logout';
                alert('Logout functionality would be implemented here');
            }
        });
    }
    
    if (userProfile) {
        userProfile.addEventListener('click', function(e) {
            if (e.target !== logoutIcon && !logoutIcon.contains(e.target)) {
                console.log('User profile clicked');
                // You can show user settings or profile menu here
            }
        });
    }
}

// Real-time updates simulation (optional)
function simulateRealTimeUpdates() {
    setInterval(() => {
        // Simulate pending orders update
        const pendingValue = document.querySelector('.stat-card:nth-child(2) .stat-value');
        if (pendingValue) {
            const currentValue = parseInt(pendingValue.textContent);
            const randomChange = Math.random() > 0.5 ? 1 : -1;
            const newValue = Math.max(0, currentValue + randomChange);
            pendingValue.textContent = newValue;
        }
    }, 10000); // Update every 10 seconds
}

// Utility function: Format currency
function formatCurrency(amount) {
    return `₱ ${amount.toLocaleString()}`;
}

// Utility function: Get current date/time
function getCurrentDateTime() {
    const now = new Date();
    return now.toLocaleString('en-US', {
        month: 'long',
        day: 'numeric',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

// Add smooth scroll behavior
document.documentElement.style.scrollBehavior = 'smooth';

// Log when dashboard is ready
console.log('KRES Admin Dashboard initialized successfully!');
console.log('Current time:', getCurrentDateTime());

// Uncomment to enable real-time updates simulation
// simulateRealTimeUpdates();
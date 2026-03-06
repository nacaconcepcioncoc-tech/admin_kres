// Inventory Page Functionality

document.addEventListener('DOMContentLoaded', function() {
    initializeNavigation();
    initializeFilterTabs();
    initializeUpdateStock();
    initializeTableActions();
    initializeAddNewItem();
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

// Filter tabs functionality
function initializeFilterTabs() {
    const filterTabs = document.querySelectorAll('.filter-tab');
    const tableRows = document.querySelectorAll('.inventory-table tbody tr');
    
    filterTabs.forEach(tab => {
        tab.addEventListener('click', function() {
            // Remove active class from all tabs
            filterTabs.forEach(t => t.classList.remove('active'));
            
            // Add active class to clicked tab
            this.classList.add('active');
            
            // Get filter value
            const filter = this.textContent.toLowerCase();
            
            // Filter table rows
            tableRows.forEach(row => {
                const category = row.querySelector('td:nth-child(2)').textContent.toLowerCase();
                
                if (filter === 'all' || category === filter) {
                    row.style.display = '';
                } else {
                    row.style.display = 'none';
                }
            });
            
            console.log(`Filtering by: ${filter}`);
        });
    });
}

// Update stock functionality
function initializeUpdateStock() {
    const itemSelect = document.getElementById('itemSelect');
    const newQuantity = document.getElementById('newQuantity');
    const updateBtn = document.querySelector('.btn-update-stock');
    
    if (updateBtn) {
        updateBtn.addEventListener('click', function() {
            const selectedItem = itemSelect.value;
            const quantity = newQuantity.value;
            
            if (!selectedItem) {
                alert('Please select an item to update.');
                return;
            }
            
            if (quantity < 0) {
                alert('Quantity cannot be negative.');
                return;
            }
            
            // Update the table
            updateTableStock(selectedItem, quantity);
            
            // Show success message
            alert(`Stock updated successfully!\nItem: ${itemSelect.options[itemSelect.selectedIndex].text}\nNew Quantity: ${quantity}`);
            
            // Reset form
            itemSelect.value = '';
            newQuantity.value = '0';
            
            console.log(`Stock updated: ${selectedItem} - ${quantity}`);
        });
    }
}

// Update table stock based on selection
function updateTableStock(itemValue, quantity) {
    const tableRows = document.querySelectorAll('.inventory-table tbody tr');
    const itemMap = {
        'red-roses': 0,
        'carnations': 1,
        'stargazer': 2,
        'gypsophila': 3,
        'eucalyptus': 4
    };
    
    const rowIndex = itemMap[itemValue];
    if (rowIndex !== undefined && tableRows[rowIndex]) {
        const row = tableRows[rowIndex];
        const stockCell = row.querySelector('.stock-count');
        const statusCell = row.querySelector('.status-badge');
        
        // Determine unit based on category
        const category = row.querySelector('td:nth-child(2)').textContent;
        const unit = category === 'Flowers' ? 'stems' : 'bundles';
        
        // Update stock count
        stockCell.textContent = `${quantity} ${unit}`;
        
        // Update status based on quantity
        let newStatus, newClass;
        if (quantity >= 100 || (category === 'Fillers' && quantity >= 20)) {
            newStatus = 'In Stock';
            newClass = 'in-stock';
        } else if (quantity >= 50 || (category === 'Fillers' && quantity >= 10)) {
            newStatus = 'Low Stock';
            newClass = 'low-stock';
        } else {
            newStatus = 'Critical';
            newClass = 'critical';
        }
        
        // Update status badge
        statusCell.className = `status-badge ${newClass}`;
        statusCell.textContent = newStatus;
        
        // Update row data attribute
        row.setAttribute('data-status', newClass);
    }
}

// Table action buttons
function initializeTableActions() {
    // Edit buttons
    const editButtons = document.querySelectorAll('.action-btn.edit');
    editButtons.forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const row = this.closest('tr');
            const itemName = row.querySelector('.item-name').textContent;
            const stock = row.querySelector('.stock-count').textContent;
            const price = row.querySelector('.price').textContent;
            
            console.log(`Editing item: ${itemName}`);
            alert(`Edit Item: ${itemName}\nCurrent Stock: ${stock}\nCurrent Price: ${price}\n\n(Edit form would open here)`);
        });
    });
    
    // Delete buttons
    const deleteButtons = document.querySelectorAll('.action-btn.delete');
    deleteButtons.forEach(btn => {
        btn.addEventListener('click', function(e) {
            e.stopPropagation();
            const row = this.closest('tr');
            const itemName = row.querySelector('.item-name').textContent;
            
            if (confirm(`Are you sure you want to delete "${itemName}"?`)) {
                // Add fade out animation
                row.style.transition = 'opacity 0.3s ease';
                row.style.opacity = '0';
                
                setTimeout(() => {
                    row.remove();
                    console.log(`Deleted item: ${itemName}`);
                    alert(`"${itemName}" has been deleted successfully.`);
                }, 300);
            }
        });
    });
    
    // Row click (optional - for viewing details)
    const tableRows = document.querySelectorAll('.inventory-table tbody tr');
    tableRows.forEach(row => {
        row.addEventListener('click', function(e) {
            // Don't trigger if clicking action buttons
            if (e.target.closest('.action-btn')) {
                return;
            }
            
            const itemName = this.querySelector('.item-name').textContent;
            const category = this.querySelector('td:nth-child(2)').textContent;
            const stock = this.querySelector('.stock-count').textContent;
            const price = this.querySelector('.price').textContent;
            const status = this.querySelector('.status-badge').textContent;
            
            console.log(`Viewing item details: ${itemName}`);
            // You can implement a modal or detail view here
        });
    });
}

// Add new item functionality
function initializeAddNewItem() {
    const addBtn = document.querySelector('.btn-add-item');
    
    if (addBtn) {
        addBtn.addEventListener('click', function() {
            console.log('Add new item clicked');
            alert('Add New Item Form\n\n(Form would open here to add a new inventory item)');
            // You can implement a modal or form here
        });
    }
}

// Logout functionality
const logoutIcon = document.querySelector('.logout-icon');
if (logoutIcon) {
    logoutIcon.addEventListener('click', function(e) {
        e.stopPropagation();
        
        if (confirm('Are you sure you want to logout?')) {
            console.log('Logging out...');
            alert('Logout functionality would be implemented here');
        }
    });
}

// Utility function: Format number with commas
function formatNumber(num) {
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

// Utility function: Check low stock items
function checkLowStock() {
    const tableRows = document.querySelectorAll('.inventory-table tbody tr');
    let lowStockCount = 0;
    const lowStockItems = [];
    
    tableRows.forEach(row => {
        const status = row.getAttribute('data-status');
        if (status === 'low-stock' || status === 'critical') {
            lowStockCount++;
            const itemName = row.querySelector('.item-name').textContent;
            lowStockItems.push(itemName);
        }
    });
    
    console.log(`Low stock items: ${lowStockCount}`);
    console.log('Items:', lowStockItems);
    
    return { count: lowStockCount, items: lowStockItems };
}

// Auto-check low stock on load
const lowStockInfo = checkLowStock();
if (lowStockInfo.count > 0) {
    console.log(`⚠️ Alert: ${lowStockInfo.count} items are running low on stock`);
}

// Add smooth scroll behavior
document.documentElement.style.scrollBehavior = 'smooth';

console.log('KRES Admin Inventory Page initialized successfully!');
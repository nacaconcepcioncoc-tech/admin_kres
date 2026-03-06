// Search functionality
document.addEventListener('DOMContentLoaded', function() {
    const searchInput = document.getElementById('searchInput');
    const tableRows = document.querySelectorAll('tbody tr');

    // Search filter
    searchInput.addEventListener('input', function() {
        const searchTerm = this.value.toLowerCase();
        
        tableRows.forEach(row => {
            const customerName = row.cells[0].textContent.toLowerCase();
            const contactNumber = row.cells[1].textContent.toLowerCase();
            
            if (customerName.includes(searchTerm) || contactNumber.includes(searchTerm)) {
                row.style.display = '';
                row.style.animation = 'fadeIn 0.3s ease';
            } else {
                row.style.display = 'none';
            }
        });
    });

    // View Profile button functionality
    const viewProfileBtns = document.querySelectorAll('.view-profile-btn');
    viewProfileBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const customerName = this.closest('tr').cells[0].textContent;
            alert(`Viewing profile for: ${customerName}`);
        });
    });

    // Add New Customer button functionality
    const addCustomerBtn = document.querySelector('.add-customer-btn');
    addCustomerBtn.addEventListener('click', function() {
        alert('Add New Customer form would open here');
    });

    // Navigation menu click handlers
    const navLinks = document.querySelectorAll('.nav-menu a');
    navLinks.forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            
            // Remove active class from all items
            document.querySelectorAll('.nav-menu li').forEach(li => {
                li.classList.remove('active');
            });
            
            // Add active class to clicked item
            this.parentElement.classList.add('active');
            
            // Get the page name
            const pageName = this.textContent.trim();
            console.log(`Navigating to: ${pageName}`);
        });
    });

    // Chat icon click handler
    const chatIcon = document.querySelector('.chat-icon');
    if (chatIcon) {
        chatIcon.addEventListener('click', function() {
            alert('Chat feature would open here');
        });
    }

    // Logout functionality
    const logoutBtn = document.querySelector('.logout-btn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', function() {
            if (confirm('Are you sure you want to logout?')) {
                alert('Logout functionality would be implemented here');
            }
        });
    }

    // Table row hover effects
    tableRows.forEach(row => {
        row.addEventListener('mouseenter', function() {
            this.style.transform = 'scale(1.01)';
            this.style.transition = 'transform 0.2s ease';
        });
        
        row.addEventListener('mouseleave', function() {
            this.style.transform = 'scale(1)';
        });
    });
});

// Add fade-in animation for filtered results
const style = document.createElement('style');
style.textContent = `
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(-10px); }
        to { opacity: 1; transform: translateY(0); }
    }
`;
document.head.appendChild(style);

// Responsive sidebar toggle (for mobile)
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    sidebar.classList.toggle('collapsed');
}

// Search suggestions (optional enhancement)
function initSearchSuggestions() {
    const customers = [
        'Lester Alcantara',
        'Chloe Elizha Borcillo', 
        'Kyntly Dumaguing'
    ];
    
    const searchInput = document.getElementById('searchInput');
    
    searchInput.addEventListener('focus', function() {
        // Could implement dropdown suggestions here
        console.log('Search focused - suggestions could be shown');
    });
}

// Initialize additional features
initSearchSuggestions();

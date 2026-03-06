// inventory-ajax.js - Connects inventory forms to Django backend

// Get CSRF token for Django
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie('csrftoken');

// ============================================================================
// ADD NEW PRODUCT (SAVE TO DATABASE)
// ============================================================================
function submitAddItem(event) {
    event.preventDefault();
    
    const form = document.getElementById('addItemForm');
    const isEdit = form.dataset.isEdit === 'true';
    const productId = form.dataset.productId;
    
    // Get form data
    const formData = {
        name: document.getElementById('itemName').value,
        category: document.getElementById('category').value,
        stock_quantity: parseInt(document.getElementById('quantity').value),
        unit: document.getElementById('unit').value,
        price: parseFloat(document.getElementById('price').value),
        low_stock_threshold: 10, // Default threshold
    };
    
    if (!isEdit) {
        formData.sku = 'SKU-' + Date.now(); // Auto-generate SKU for new items
    }
    
    const url = isEdit ? `/ajax/product/${productId}/update/` : '/ajax/product/create/';
    const method = 'POST';
    
    // Send to Django backend via AJAX
    fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrftoken,
        },
        body: JSON.stringify(formData),
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Silent success - just reload
            console.log('Product saved successfully');
            
            // Reset form state
            form.dataset.isEdit = 'false';
            delete form.dataset.productId;
            const submitBtn = document.querySelector('#addItemForm button[type="submit"]');
            if (submitBtn) {
                submitBtn.textContent = 'Add Item';
            }
            
            // Reload page to show new/updated product from database
            setTimeout(() => window.location.reload(), 300);
        } else {
            console.error('Error:', data.message);
        }
    })
    .catch(error => {
        console.error('Error:', error);
    });
}

// ============================================================================
// UPDATE STOCK QUANTITY (SAVE TO DATABASE)
// ============================================================================
function updateStockQuantity() {
    const productSelect = document.getElementById('itemSelect');
    const productId = productSelect.value;
    const newQuantity = parseInt(document.getElementById('newQuantity').value);
    
    if (!productId) {
        console.warn('Please select a product');
        return;
    }
    
    // Send to Django backend via AJAX
    const updateData = {
        product_id: productId,
        stock_quantity: newQuantity,
    };
    
    fetch('/ajax/product/update-stock/', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrftoken,
        },
        body: JSON.stringify(updateData),
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Silent success - just reload
            console.log('Stock updated successfully');
            
            // Reload page to show updated stock from database
            setTimeout(() => window.location.reload(), 300);
        } else {
            console.error('Error:', data.message);
        }
    })
    .catch(error => {
        console.error('Error:', error);
    });
}

// ============================================================================
// LOAD PRODUCTS FROM DATABASE ON PAGE LOAD
// ============================================================================
document.addEventListener('DOMContentLoaded', function() {
    // Load products from Django backend
    loadProductsFromDatabase();
});

function loadProductsFromDatabase() {
    fetch('/ajax/products/list/')
        .then(response => response.json())
        .then(products => {
            // Populate product dropdown
            const productSelect = document.getElementById('itemSelect');
            if (productSelect) {
                productSelect.innerHTML = '<option value="">Select Product</option>';
                products.forEach(product => {
                    const option = document.createElement('option');
                    option.value = product.product_id;
                    option.textContent = `${product.name} (Stock: ${product.stock_quantity})`;
                    productSelect.appendChild(option);
                });
            }
        })
        .catch(error => {
            console.error('Error loading products:', error);
        });
}

// ============================================================================
// MODAL FUNCTIONS
// ============================================================================
function openAddItemModal() {
    document.getElementById('addItemModal').style.display = 'block';
}

function closeAddItemModal() {
    document.getElementById('addItemModal').style.display = 'none';
    document.getElementById('addItemForm').reset();
}

// Close modal when clicking outside
window.onclick = function(event) {
    const modal = document.getElementById('addItemModal');
    if (event.target === modal) {
        closeAddItemModal();
    }
}

// ============================================================================
// EDIT PRODUCT
// ============================================================================
function editItem(productId) {
    console.log('Edit item:', productId);
    // Fetch product data
    fetch(`/ajax/product/${encodeURIComponent(productId)}/`)
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const product = data.product;
                // Populate form with product data
                document.getElementById('itemName').value = product.name;
                document.getElementById('category').value = product.category;
                document.getElementById('quantity').value = product.stock_quantity;
                document.getElementById('unit').value = product.unit;
                document.getElementById('price').value = product.price;
                // Store product ID for update operation
                document.getElementById('addItemForm').dataset.productId = productId;
                document.getElementById('addItemForm').dataset.isEdit = 'true';
                // Change form title/button text to indicate edit mode
                const submitBtn = document.querySelector('#addItemForm button[type="submit"]');
                if (submitBtn) {
                    submitBtn.textContent = 'Update Item';
                }
                openAddItemModal();
            }
        })
        .catch(error => {
            console.error('Error loading product:', error);
            alert('Failed to load product details');
        });
}

// ============================================================================
// DELETE PRODUCT
// ============================================================================
function deleteItem(productId) {
    if (confirm('Are you sure you want to delete this item?')) {
        fetch(`/ajax/product/${encodeURIComponent(productId)}/delete/`, {
            method: 'DELETE',
            headers: {
                'X-CSRFToken': csrftoken,
            },
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                console.log('Product deleted successfully');
                setTimeout(() => window.location.reload(), 300);
            } else {
                console.error('Error:', data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
        });
    }
}

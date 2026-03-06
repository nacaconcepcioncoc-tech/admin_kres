// reports-ajax.js - Connects reports to Django backend

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
// LOAD REPORTS DATA ON PAGE LOAD
// ============================================================================
document.addEventListener('DOMContentLoaded', function() {
    // Reports data is already loaded in the page from Django template
    // This file provides real-time chart updates and interactions
    
    loadReportCharts();
    initializeReportInteractions();
});

// ============================================================================
// LOAD REPORT CHARTS
// ============================================================================
function loadReportCharts() {
    // Sales Chart - if Chart.js library is available
    const salesChartCanvas = document.getElementById('salesChart');
    if (salesChartCanvas && typeof Chart !== 'undefined') {
        // Get data from page
        const dates = [];
        const sales = [];
        
        // Parse data from table rows or dataset
        const chartDataStr = salesChartCanvas.getAttribute('data-chart');
        if (chartDataStr) {
            try {
                const chartData = JSON.parse(decodeURIComponent(chartDataStr));
                chartData.forEach(item => {
                    dates.push(item.date);
                    sales.push(item.sales);
                });
            } catch (e) {
                console.error('Error parsing chart data:', e);
            }
        }
        
        if (dates.length > 0) {
            const ctx = salesChartCanvas.getContext('2d');
            new Chart(ctx, {
                type: 'line',
                data: {
                    labels: dates,
                    datasets: [{
                        label: 'Daily Sales (₱)',
                        data: sales,
                        borderColor: '#3d5a73',
                        backgroundColor: 'rgba(61, 90, 115, 0.1)',
                        borderWidth: 2,
                        fill: true,
                        tension: 0.4,
                        pointRadius: 4,
                        pointBackgroundColor: '#3d5a73',
                        pointBorderColor: '#fff',
                        pointBorderWidth: 2,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: {
                            display: true,
                            position: 'top',
                        },
                        title: {
                            display: true,
                            text: '7-Day Sales Trend'
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                callback: function(value) {
                                    return '₱' + value.toLocaleString();
                                }
                            }
                        }
                    }
                }
            });
        }
    }
    
    // Payment Methods Pie Chart
    const paymentChartCanvas = document.getElementById('paymentChart');
    if (paymentChartCanvas && typeof Chart !== 'undefined') {
        const methods = [];
        const amounts = [];
        const colors = ['#3d5a73', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];
        
        const paymentTableRows = document.querySelectorAll('#paymentMethodsTable tbody tr');
        let colorIndex = 0;
        
        paymentTableRows.forEach(row => {
            const method = row.cells[0]?.textContent.trim();
            const amount = parseFloat(row.cells[1]?.textContent.replace(/[^\d.]/g, '')) || 0;
            
            if (method && amount > 0) {
                methods.push(method);
                amounts.push(amount);
            }
        });
        
        if (methods.length > 0) {
            const ctx = paymentChartCanvas.getContext('2d');
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: methods,
                    datasets: [{
                        data: amounts,
                        backgroundColor: colors.slice(0, methods.length),
                        borderColor: '#fff',
                        borderWidth: 2,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            position: 'bottom',
                        },
                        title: {
                            display: true,
                            text: 'Payment Methods Distribution'
                        }
                    }
                }
            });
        }
    }
    
    // Inventory Status Pie Chart
    const inventoryChartCanvas = document.getElementById('inventoryChart');
    if (inventoryChartCanvas && typeof Chart !== 'undefined') {
        const lowStockCount = document.querySelector('[data-low-stock]')?.getAttribute('data-low-stock') || 0;
        const outOfStockCount = document.querySelector('[data-out-of-stock]')?.getAttribute('data-out-of-stock') || 0;
        const activeProductsCount = document.querySelector('[data-active-products]')?.getAttribute('data-active-products') || 0;
        
        if (activeProductsCount > 0 || lowStockCount > 0 || outOfStockCount > 0) {
            const ctx = inventoryChartCanvas.getContext('2d');
            new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['In Stock', 'Low Stock', 'Out of Stock'],
                    datasets: [{
                        data: [
                            parseInt(activeProductsCount) - parseInt(lowStockCount) - parseInt(outOfStockCount),
                            parseInt(lowStockCount),
                            parseInt(outOfStockCount)
                        ],
                        backgroundColor: ['#10b981', '#f59e0b', '#ef4444'],
                        borderColor: '#fff',
                        borderWidth: 2,
                    }]
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: {
                            position: 'bottom',
                        },
                        title: {
                            display: true,
                            text: 'Inventory Status'
                        }
                    }
                }
            });
        }
    }
}

// ============================================================================
// INITIALIZE REPORT INTERACTIONS
// ============================================================================
function initializeReportInteractions() {
    // Add export functionality
    const exportPdfBtn = document.getElementById('exportPdf');
    if (exportPdfBtn) {
        exportPdfBtn.addEventListener('click', function() {
            // For now, use browser print
            window.print();
        });
    }
    
    // Add refresh button
    const refreshBtn = document.getElementById('refreshReports');
    if (refreshBtn) {
        refreshBtn.addEventListener('click', function() {
            location.reload();
        });
    }
    
    // Add date range filter (if exists)
    const dateRangeSelect = document.getElementById('dateRange');
    if (dateRangeSelect) {
        dateRangeSelect.addEventListener('change', function() {
            // Reload with new date range
            const range = this.value;
            // Could implement custom date range loading here
        });
    }
    
    // Click handlers for table rows if detailed view exists
    const tableRows = document.querySelectorAll('.reports-table tbody tr');
    tableRows.forEach(row => {
        row.addEventListener('click', function() {
            // Could show detailed modal/view
            console.log('Row clicked:', this.textContent);
        });
    });
}

// ============================================================================
// REFRESH REPORTS DATA
// ============================================================================
function refreshReportsData() {
    console.log('Refreshing reports data...');
    loadReportCharts();
    console.log('Reports data refreshed');
}

// ============================================================================
// EXPORT FUNCTIONS
// ============================================================================
function exportReportsToPDF() {
    // Open print dialog
    window.print();
}

function exportReportsToCSV() {
    const tables = document.querySelectorAll('.reports-table');
    let csv = '';
    
    tables.forEach((table, tableIndex) => {
        if (tableIndex > 0) csv += '\n\n';
        
        // Header
        const headers = [];
        table.querySelectorAll('thead th').forEach(th => {
            headers.push(th.textContent.trim());
        });
        csv += headers.join(',') + '\n';
        
        // Rows
        table.querySelectorAll('tbody tr').forEach(tr => {
            const row = [];
            tr.querySelectorAll('td').forEach(td => {
                row.push('"' + td.textContent.trim().replace(/"/g, '""') + '"');
            });
            csv += row.join(',') + '\n';
        });
    });
    
    // Download
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `reports-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
}

console.log('Reports AJAX module loaded successfully!');

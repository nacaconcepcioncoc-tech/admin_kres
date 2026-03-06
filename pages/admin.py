from django.contrib import admin
from django.utils.html import format_html
from .models import Customer, Product, Order, OrderItem, Payment, StockAlert


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['customer_id', 'first_name', 'last_name', 'phone', 'created_at', 'get_total_product_orders', 'get_customer_payment']
    list_filter = ['created_at', 'city', 'state']
    search_fields = ['first_name', 'last_name', 'phone']
    readonly_fields = ['customer_id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Personal Information', {
            'fields': ('first_name', 'last_name', 'phone')
        }),
        ('Address', {
            'fields': ('address', 'city', 'state', 'zip_code')
        }),
        ('System Information', {
            'fields': ('customer_id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_total_product_orders(self, obj):
        """Get product names ordered by this customer"""
        product_names = []
        for order in obj.orders.all():
            for item in order.items.all():
                product_names.append(item.product_name)
        return ', '.join(product_names) if product_names else '—'
    get_total_product_orders.short_description = 'Products Ordered'
    
    def get_customer_payment(self, obj):
        """Get total payment amount from customer across all orders"""
        total_payment = sum(payment.amount for order in obj.orders.all() for payment in order.payments.all())
        return f"₱{total_payment:,.2f}"
    get_customer_payment.short_description = 'Total Payment'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['product_id', 'name', 'category', 'price', 'stock_quantity', 
                    'get_stock_status', 'updated_at']
    list_filter = ['category', 'is_active', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['product_id', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Product Information', {
            'fields': ('name', 'description', 'category')
        }),
        ('Pricing', {
            'fields': ('price', 'cost_price')
        }),
        ('Inventory', {
            'fields': ('stock_quantity', 'low_stock_threshold', 'unit', 'is_active')
        }),
        ('System Information', {
            'fields': ('product_id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_queryset(self, request):
        """Filter to only show active Flowers and Fillers inventory items"""
        qs = super().get_queryset(request)
        return qs.filter(
            is_active=True,
            category__in=['FLOWERS', 'FILLERS']
        ).exclude(sku__startswith='CUSTOM-').order_by('name')
    
    def get_stock_status(self, obj):
        status = obj.get_stock_status()
        if status == "Out of Stock":
            color = 'red'
        elif status == "Low Stock":
            color = 'orange'
        else:
            color = 'green'
        return format_html(
            '<span style="color: {}; font-weight: bold;">{}</span>',
            color, status
        )
    get_stock_status.short_description = 'Stock Status'
    
    def save_model(self, request, obj, form, change):
        """Check for stock alerts after saving"""
        super().save_model(request, obj, form, change)
        StockAlert.check_and_create_alerts()


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 1
    fields = ['product', 'quantity', 'unit_price', 'get_total']
    readonly_fields = ['get_total']
    
    def get_total(self, obj):
        if obj.pk:
            return f"₱{obj.get_total_price():,.2f}"
        return "-"
    get_total.short_description = 'Total'


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'get_customer_display', 'status', 'get_order_total', 'get_total_items', 
                    'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['order_number', 'customer__first_name', 'customer__last_name']
    readonly_fields = ['order_id', 'order_number', 'subtotal', 'total', 'created_at', 'updated_at']
    inlines = [OrderItemInline]
    
    fieldsets = (
        ('Order Information', {
            'fields': ('order_number', 'customer', 'status', 'notes')
        }),
        ('Order Totals', {
            'fields': ('subtotal', 'tax', 'discount', 'total')
        }),
        ('System Information', {
            'fields': ('order_id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_customer_display(self, obj):
        """Display customer name cleanly without extra info"""
        return f"{obj.customer.first_name} {obj.customer.last_name}"
    get_customer_display.short_description = 'Customer'
    
    def get_order_total(self, obj):
        return f"₱{obj.total:,.2f}"
    get_order_total.short_description = 'Total'
    
    def get_total_items(self, obj):
        """Get product names from order items"""
        product_names = [item.product_name for item in obj.items.all()]
        return ', '.join(product_names) if product_names else '—'
    get_total_items.short_description = 'Total Items'
    
    def save_formset(self, request, form, formset, change):
        """Recalculate totals after saving order items"""
        instances = formset.save(commit=False)
        for instance in instances:
            instance.save()
        formset.save_m2m()
        
        # Recalculate order totals
        if form.instance.pk:
            form.instance.calculate_totals()


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['get_customer_name', 'get_order_number', 'amount', 'payment_method', 'get_payment_status', 'payment_date']
    list_filter = ['payment_method', 'payment_status', 'created_at']
    search_fields = ['order__order_number', 'order__customer__first_name', 'order__customer__last_name', 'transaction_id']
    readonly_fields = ['payment_id', 'payment_number', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Payment Information', {
            'fields': ('payment_number', 'order', 'amount', 'payment_method', 'payment_status')
        }),
        ('Transaction Details', {
            'fields': ('transaction_id', 'payment_date', 'notes')
        }),
        ('System Information', {
            'fields': ('payment_id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_customer_name(self, obj):
        """Get customer name from order"""
        return f"{obj.order.customer.first_name} {obj.order.customer.last_name}"
    get_customer_name.short_description = 'Customer'
    
    def get_order_number(self, obj):
        """Get order number only"""
        return obj.order.order_number
    get_order_number.short_description = 'Order'
    
    def get_payment_status(self, obj):
        """Get payment status matching frontend display logic"""
        if obj.order.status == 'completed':
            return '✅ Completed'
        elif obj.payment_status == 'completed':
            return '💳 Fully Paid'
        else:
            return '💰 Down Payment'
    get_payment_status.short_description = 'Payment Status'


@admin.register(StockAlert)
class StockAlertAdmin(admin.ModelAdmin):
    list_display = ['get_product_name', 'get_alert_type', 'stock_level_at_alert', 'created_at']
    list_filter = ['alert_type', 'alert_status', 'created_at']
    search_fields = ['product__name']
    readonly_fields = ['alert_id', 'created_at', 'resolved_at']
    
    def get_product_name(self, obj):
        """Get product name without helper text"""
        return obj.product.name
    get_product_name.short_description = 'Product'
    
    def get_alert_type(self, obj):
        """Get alert type without helper text"""
        return obj.get_alert_type_display()
    get_alert_type.short_description = 'Alert Type'
    
    def has_add_permission(self, request):
        # Prevent manual creation of alerts
        return False

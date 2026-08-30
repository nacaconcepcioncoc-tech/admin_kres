from django.contrib import admin
from django import forms
from .models import (
    Customer,
    Order,
    OrderItem,
    Payment,
    Employee,
    EmployeeMonthlyPerformance,
    EmployeeStandingPin,
)
from .payment_state import calculate_order_payment_display_state


class EmployeeStandingPinAdminForm(forms.ModelForm):
    new_pin = forms.RegexField(
        regex=r'^\d{4,12}$',
        required=False,
        strip=True,
        widget=forms.PasswordInput(render_value=False),
        help_text='Enter 4–12 digits. Leave blank when editing to keep the current PIN.',
    )
    confirm_pin = forms.CharField(
        required=False,
        strip=True,
        widget=forms.PasswordInput(render_value=False),
    )

    class Meta:
        model = EmployeeStandingPin
        fields = ('is_active',)

    def clean(self):
        cleaned = super().clean()
        new_pin = cleaned.get('new_pin')
        if not self.instance.pk and not new_pin:
            self.add_error('new_pin', 'A PIN is required.')
        if new_pin != cleaned.get('confirm_pin'):
            self.add_error('confirm_pin', 'The PINs do not match.')
        return cleaned


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
    search_fields = ['order_number', 'sender_name', 'receiver_name', 'customer__first_name', 'customer__last_name']
    readonly_fields = ['order_id', 'order_number', 'subtotal', 'total', 'created_at', 'updated_at']
    inlines = [OrderItemInline]
    
    fieldsets = (
        ('Order Information', {
            'fields': ('order_number', 'customer', 'status', 'notes')
        }),
        ('Sender / Customer Information', {
            'fields': ('sender_name', 'sender_phone', 'sender_is_receiver')
        }),
        ('Receiver Information', {
            'fields': ('receiver_name', 'customer_phone', 'customer_address', 'delivery_address')
        }),
        ('Order Totals', {
            'fields': ('subtotal', 'total')
        }),
        ('System Information', {
            'fields': ('order_id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def get_customer_display(self, obj):
        """Display customer name cleanly without extra info"""
        return obj.customer_name
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
        return obj.order.customer_name
    get_customer_name.short_description = 'Customer'
    
    def get_order_number(self, obj):
        """Get order number only"""
        return obj.order.order_number
    get_order_number.short_description = 'Order'
    
    def get_payment_status(self, obj):
        """Get payment status matching frontend display logic"""
        state = calculate_order_payment_display_state(
            obj.order,
            obj.order.payments.all(),
        )
        return state['label']
    get_payment_status.short_description = 'Payment Status'




class EmployeeMonthlyPerformanceInline(admin.TabularInline):
    model = EmployeeMonthlyPerformance
    extra = 0
    fields = ('year', 'month', 'stars', 'demerits', 'admin_remarks', 'updated_at')
    readonly_fields = ('updated_at',)
    ordering = ('-year', '-month')

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'full_name',
        'position',
        'monthly_points',
        'total_stars',
        'total_demerits',
        'performance_status',
        'updated_at',
    )
    list_filter = ('position',)
    inlines = (EmployeeMonthlyPerformanceInline,)
    search_fields = ('full_name', 'position')
    readonly_fields = (
        'id',
        'monthly_points',
        'total_stars',
        'total_demerits',
        'performance_status',
        'created_at',
        'updated_at',
    )

    fieldsets = (
        ('Employee Profile', {
            'fields': ('full_name', 'position', 'profile_picture_url')
        }),
        ('Performance Summary', {
            'fields': ('monthly_points', 'total_stars', 'total_demerits', 'performance_status')
        }),
        ('System Information', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )




@admin.register(EmployeeMonthlyPerformance)
class EmployeeMonthlyPerformanceAdmin(admin.ModelAdmin):
    list_display = (
        'employee', 'year', 'month', 'stars', 'demerits', 'updated_at'
    )
    list_filter = ('year', 'month')
    search_fields = ('employee__full_name', 'employee__position', 'admin_remarks')
    autocomplete_fields = ('employee',)
    readonly_fields = ('created_at', 'updated_at')
    ordering = ('-year', '-month', 'employee__full_name')
    fieldsets = (
        ('Evaluation', {
            'fields': ('employee', 'year', 'month', 'stars', 'demerits')
        }),
        ('Admin Remarks', {'fields': ('admin_remarks',)}),
        ('System Information', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def delete_queryset(self, request, queryset):
        employee_ids = list(queryset.values_list('employee_id', flat=True).distinct())
        super().delete_queryset(request, queryset)
        for employee in Employee.objects.filter(pk__in=employee_ids):
            employee.recalculate_yearly_evaluation_summary()


@admin.register(EmployeeStandingPin)
class EmployeeStandingPinAdmin(admin.ModelAdmin):
    form = EmployeeStandingPinAdminForm
    list_display = ('id', 'is_active', 'updated_at', 'updated_by')
    readonly_fields = ('updated_at', 'updated_by')
    fields = ('new_pin', 'confirm_pin', 'is_active', 'updated_at', 'updated_by')

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def save_model(self, request, obj, form, change):
        new_pin = form.cleaned_data.get('new_pin')
        if new_pin:
            obj.set_pin(new_pin)
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

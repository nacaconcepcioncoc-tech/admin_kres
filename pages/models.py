from django.db import models
from django.core.validators import MinValueValidator
from django.utils import timezone
from decimal import Decimal




class Customer(models.Model):
    """Customer model - stores customer information"""
    customer_id = models.AutoField(primary_key=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
   
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Customer'
        verbose_name_plural = 'Customers'
   
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"
   
    def save(self, *args, **kwargs):
        """Auto-capitalize customer names when saving"""
        self.first_name = self.first_name.upper() if self.first_name else self.first_name
        self.last_name = self.last_name.upper() if self.last_name else self.last_name
        super().save(*args, **kwargs)
   
    def get_total_orders(self):
        """Get total number of orders for this customer"""
        return self.orders.count()
   
    def get_total_spent(self):
        """Get total amount spent by this customer"""
        return sum(order.get_total_amount() for order in self.orders.all())




class Product(models.Model):
    """Product/Inventory model - stores product information and stock levels"""
    product_id = models.AutoField(primary_key=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    sku = models.CharField(max_length=100, unique=True, help_text="Stock Keeping Unit")
    category = models.CharField(max_length=100, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
                                     help_text="Cost price for profit calculation", blank=True, null=True)
    stock_quantity = models.IntegerField(default=0, validators=[MinValueValidator(0)],
                                         help_text="Current stock quantity (manually updated)")
    low_stock_threshold = models.IntegerField(default=10, validators=[MinValueValidator(0)],
                                              help_text="Alert when stock falls below this level")
    unit = models.CharField(max_length=50, default="pcs", help_text="Unit of measurement (pcs, kg, box, etc.)")
    is_active = models.BooleanField(default=True, help_text="Is this product available for sale?")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
   
    class Meta:
        ordering = ['name']
        verbose_name = 'Product'
        verbose_name_plural = 'Products'
   
    def __str__(self):
        return f"{self.name} ({self.sku})"
   
    def save(self, *args, **kwargs):
        """Auto-capitalize product name and category when saving"""
        self.name = self.name.upper() if self.name else self.name
        self.category = self.category.upper() if self.category else self.category
        super().save(*args, **kwargs)
   
    def is_low_stock(self):
        """Check if product is low on stock"""
        return self.stock_quantity <= self.low_stock_threshold
   
    def is_out_of_stock(self):
        """Check if product is out of stock"""
        return self.stock_quantity == 0
   
    def get_stock_status(self):
        """Get stock status as string"""
        if self.is_out_of_stock():
            return "Out of Stock"
        elif self.is_low_stock():
            return "Low Stock"
        return "In Stock"




class Order(models.Model):
    """Order model - stores order information"""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
   
    order_id = models.AutoField(primary_key=True)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='orders')
    order_number = models.CharField(max_length=50, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True, help_text="Additional order notes")
    delivery_date = models.DateField(null=True, blank=True, help_text="Requested delivery date")
    delivery_time = models.TimeField(null=True, blank=True, help_text="Requested delivery time")
    customer_phone = models.CharField(max_length=20, blank=True, help_text="Contact number for this order")
    customer_address = models.TextField(blank=True, help_text="Delivery address for this order")
    fulfilled_by = models.CharField(max_length=100, blank=True, default='',
                                    help_text="Who fulfilled the order (staff name)")
    
    # Sender information (who is sending the delivery)
    sender_name = models.CharField(max_length=100, blank=True, help_text="Name of person sending the order")
    sender_phone = models.CharField(max_length=20, blank=True, help_text="Contact number of sender")
    
    # Rider information (delivery rider details) 
    rider_name = models.CharField(max_length=100, blank=True, help_text="Name of delivery rider")
    rider_phone = models.CharField(max_length=20, blank=True, help_text="Contact number of rider")
    rider_vehicle = models.CharField(max_length=100, blank=True, help_text="Vehicle used for delivery (e.g. motorcycle, car)")
   
    # Order totals (calculated from order items)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
   
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
   
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'
   
    def __str__(self):
        return f"Order {self.order_number} - {self.customer.first_name} {self.customer.last_name}"
   
    def save(self, *args, **kwargs):
        """Generate order number if not exists"""
        if not self.order_number:
            # Use timezone.now() for date since created_at (auto_now_add) is not yet set on first save
            from django.utils import timezone
            order_date = timezone.localtime(timezone.now()).strftime('%Y%m%d')
            last_order = Order.objects.order_by('-order_id').first()
            if last_order:
                last_num = int(last_order.order_id)
                new_num = last_num + 1
            else:
                new_num = 1
            self.order_number = f'ORD-{new_num:04d}-{order_date}'
        super().save(*args, **kwargs)
   
    def calculate_totals(self):
        """Calculate order totals from order items"""
        items = self.items.all()
        self.subtotal = sum(item.get_total_price() for item in items)
        self.total = self.subtotal + self.tax - self.discount
        self.save()
   
    def get_total_amount(self):
        """Get total order amount"""
        return self.total
   
    def get_total_items(self):
        """Get total number of items in order"""
        return sum(item.quantity for item in self.items.all())
    
    @property
    def is_delivery_tomorrow(self):
        """Check if delivery date is tomorrow in Manila timezone"""
        if not self.delivery_date:
            return False
        # Import here to avoid circular imports
        from .manila_tz_utils import is_delivery_tomorrow
        return is_delivery_tomorrow(self.delivery_date)
    
    @property
    def is_delivery_today(self):
        """Check if delivery date is today in Manila timezone"""
        if not self.delivery_date:
            return False
        # Import here to avoid circular imports
        from .manila_tz_utils import is_delivery_today
        return is_delivery_today(self.delivery_date)
    
    def get_delivery_date_note(self):
        """Get a human-readable note about delivery date (Today/Tomorrow/Date)"""
        from .manila_tz_utils import get_delivery_date_note
        return get_delivery_date_note(self.delivery_date)




class OrderItem(models.Model):
    """Order Item model - stores individual items in an order"""
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True, related_name='order_items')
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
   
    # Store product details at time of order (in case product details change later)
    product_name = models.CharField(max_length=200)
    product_sku = models.CharField(max_length=100)
   
    class Meta:
        verbose_name = 'Order Item'
        verbose_name_plural = 'Order Items'
   
    def __str__(self):
        return f"{self.product_name} x {self.quantity}"
   
    def save(self, *args, **kwargs):
        """Store product details when saving"""
        if not self.product_name:
            self.product_name = self.product.name
        if not self.product_sku:
            self.product_sku = self.product.sku
        if not self.unit_price:
            self.unit_price = self.product.price
        super().save(*args, **kwargs)
   
    def get_total_price(self):
        """Get total price for this item"""
        return self.quantity * self.unit_price




class Payment(models.Model):
    """Payment model - stores payment information"""
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('credit_card', 'Credit Card'),
        ('debit_card', 'Debit Card'),
        ('bank_transfer', 'Bank Transfer'),
        ('gcash', 'GCash'),
        ('paymaya', 'PayMaya'),
        ('other', 'Other'),
    ]
   
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]
   
    payment_id = models.AutoField(primary_key=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payments')
    payment_number = models.CharField(max_length=50, unique=True, editable=False)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='cash')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending')
    transaction_id = models.CharField(max_length=100, blank=True, help_text="External transaction ID")
    notes = models.TextField(blank=True)
    payment_date = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
   
    class Meta:
        ordering = ['-payment_date']
        verbose_name = 'Payment'
        verbose_name_plural = 'Payments'
   
    def __str__(self):
        return f"Payment {self.payment_number} - {self.order.order_number}"
   
    def save(self, *args, **kwargs):
        """Generate payment number if not exists"""
        if not self.payment_number:
            # Generate payment number: PAY-YYYYMMDD-XXXX
            today = timezone.now().strftime('%Y%m%d')
            last_payment = Payment.objects.filter(payment_number__startswith=f'PAY-{today}').order_by('-payment_number').first()
            if last_payment:
                last_num = int(last_payment.payment_number.split('-')[-1])
                new_num = last_num + 1
            else:
                new_num = 1
            self.payment_number = f'PAY-{today}-{new_num:04d}'
        super().save(*args, **kwargs)




class StockAlert(models.Model):
    """Stock Alert model - tracks low stock alerts"""
    ALERT_TYPE_CHOICES = [
        ('low_stock', 'Low Stock'),
        ('out_of_stock', 'Out of Stock'),
    ]
   
    ALERT_STATUS_CHOICES = [
        ('active', 'Active'),
        ('resolved', 'Resolved'),
        ('ignored', 'Ignored'),
    ]
   
    alert_id = models.AutoField(primary_key=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='alerts')
    alert_type = models.CharField(max_length=20, choices=ALERT_TYPE_CHOICES)
    alert_status = models.CharField(max_length=20, choices=ALERT_STATUS_CHOICES, default='active')
    stock_level_at_alert = models.IntegerField()
    message = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
   
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Stock Alert'
        verbose_name_plural = 'Stock Alerts'
   
    def __str__(self):
        return f"{self.alert_type} - {self.product.name}"
   
    @classmethod
    def check_and_create_alerts(cls):
        """Check all products and create alerts for low/out of stock items"""
        from django.db.models import Q
       
        products = Product.objects.filter(is_active=True)
        for product in products:
            # Check if there's already an active alert for this product
            existing_alert = cls.objects.filter(
                product=product,
                alert_status='active'
            ).exists()
           
            if not existing_alert:
                if product.is_out_of_stock():
                    cls.objects.create(
                        product=product,
                        alert_type='out_of_stock',
                        stock_level_at_alert=product.stock_quantity,
                        message=f"{product.name} is out of stock!"
                    )
                elif product.is_low_stock():
                    cls.objects.create(
                        product=product,
                        alert_type='low_stock',
                        stock_level_at_alert=product.stock_quantity,
                        message=f"{product.name} stock is low ({product.stock_quantity} {product.unit} remaining)"
                    )




class MonthlySalesArchive(models.Model):
    """Archives monthly sales data to preserve it after orders are deleted on the first of each month"""
    archive_id = models.AutoField(primary_key=True)
    month_name = models.CharField(max_length=20)  # 'January', 'February', etc.
    year = models.IntegerField()
    sales_by_day = models.JSONField(default=dict, help_text="Sales amounts by day {day: amount}")
    orders_by_day = models.JSONField(default=dict, help_text="Order details by day {day: [order_data, ...]}")
    total_sales = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-year', '-archive_id']
        verbose_name = 'Monthly Sales Archive'
        verbose_name_plural = 'Monthly Sales Archives'
        unique_together = ('month_name', 'year')
    
    def __str__(self):
        return f"{self.month_name} {self.year} Sales Archive"




class YearlySalesSnapshot(models.Model):
    """Stores complete yearly sales calendar data (all 12 months) before annual reset on Jan 1"""
    snapshot_id = models.AutoField(primary_key=True)
    year = models.IntegerField(unique=True)
    calendar_data = models.JSONField(default=dict, help_text="Complete 12-month sales calendar data")
    all_months_archive = models.JSONField(default=dict, help_text="Archive of all monthly sales")
    total_yearly_sales = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-year']
        verbose_name = 'Yearly Sales Snapshot'
        verbose_name_plural = 'Yearly Sales Snapshots'
    
    def __str__(self):
        return f"Sales Snapshot - Year {self.year}"
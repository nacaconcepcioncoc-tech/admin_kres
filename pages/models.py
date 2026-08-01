from django.db import models
from django.db.models import Sum
from django.conf import settings
from urllib.parse import quote
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils import timezone
from django.contrib.auth.hashers import check_password, make_password
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
    receiver_name = models.CharField(max_length=200, blank=True, help_text="Name of the delivery receiver")
    customer_address = models.TextField(blank=True, help_text="Receiver address for this order")
    delivery_address = models.TextField(blank=True, help_text="Drop-off address for this order")
    fulfilled_by = models.CharField(max_length=100, blank=True, default='',
                                    help_text="Who fulfilled the order (staff name)")
    
    # Sender information (who is sending the delivery)
    sender_name = models.CharField(max_length=100, blank=True, help_text="Name of person sending the order")
    sender_phone = models.CharField(max_length=20, blank=True, help_text="Contact number of sender")
    sender_address = models.TextField(blank=True, help_text="Complete address of sender")
    sender_is_receiver = models.BooleanField(default=False, help_text="Sender and receiver are the same person")
    
    # Rider information (delivery rider details) 
    rider_name = models.CharField(max_length=100, blank=True, help_text="Name of delivery rider")
    rider_phone = models.CharField(max_length=20, blank=True, help_text="Contact number of rider")
    rider_vehicle = models.CharField(max_length=100, blank=True, help_text="Vehicle used for delivery (e.g. motorcycle, car)")
    delivery_fee_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Manually entered delivery fee charge"
    )

    balance_payment = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Manually entered remaining balance payment"
    )
    additional_payment = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Manual additional payment note or amount, such as balance payment plus DF charge"
    )
   
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
   
    @property
    def customer_name(self):
        """The sender is the buyer/customer; fall back only for legacy rows."""
        sender = (self.sender_name or '').strip()
        if sender:
            return sender
        return f"{self.customer.first_name} {self.customer.last_name}".strip()

    @property
    def customer_contact(self):
        """Customer contact follows the sender identity."""
        return (self.sender_phone or self.customer.phone or '').strip()

    def __str__(self):
        return f"Order {self.order_number} - {self.customer_name}"
   
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
        if self.product is None and (not self.product_name or not self.product_sku or self.unit_price is None):
            raise ValueError("OrderItem requires product details when product is not set.")

        if not self.product_name:
            self.product_name = self.product.name
        if not self.product_sku:
            self.product_sku = self.product.sku
        if self.unit_price is None:
            self.unit_price = self.product.price
        super().save(*args, **kwargs)
   
    def get_total_price(self):
        """Get total price for this item"""
        return self.quantity * self.unit_price




class Payment(models.Model):
    """Payment model - stores payment information"""
    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('gcash_james', 'GCash - James'),
        ('gcash_banban', 'GCash - Banban'),
        ('gcash_kysan', 'GCash - Kysan'),
        ('rcbc', 'RCBC'),
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




class Employee(models.Model):
    """Employee profile and live Manila-month performance summary."""
    STATUS_NOT_EVALUATED = 'Not Yet Evaluated'
    STATUS_NO_AWARD = 'No Award'
    STATUS_GOOD = 'Good'
    STATUS_EXCELLENT = 'Excellent'
    STATUS_NEEDS_IMPROVEMENT = 'Needs Improvement'

    id = models.BigAutoField(primary_key=True)
    full_name = models.CharField(max_length=150)
    position = models.CharField(max_length=120)
    profile_picture_url = models.URLField(blank=True, null=True)
    monthly_points = models.IntegerField(default=0)
    total_stars = models.PositiveSmallIntegerField(default=0)
    total_demerits = models.PositiveSmallIntegerField(default=0)
    performance_status = models.CharField(max_length=40, default=STATUS_NOT_EVALUATED)
    overall_rating = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal('3.00'))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'pages_employee'
        ordering = ['full_name']

    def __str__(self):
        return self.full_name

    @property
    def profile_photo_url(self):
        value = (self.profile_picture_url or '').strip()
        if not value:
            return ''
        if value.startswith(('http://', 'https://', '/')):
            return value
        supabase_url = getattr(settings, 'SUPABASE_URL', '').rstrip('/')
        bucket = getattr(settings, 'SUPABASE_EMPLOYEE_PHOTOS_BUCKET', 'employee-profiles').strip('/')
        if not supabase_url:
            return value
        object_path = value.lstrip('/')
        prefix = f'{bucket}/'
        if object_path.startswith(prefix):
            object_path = object_path[len(prefix):]
        return f'{supabase_url}/storage/v1/object/public/{bucket}/{quote(object_path, safe="/")}'

    @property
    def employee_id(self):
        return self.pk

    @property
    def job_position(self):
        return self.position

    @staticmethod
    def result_for_points(points, evaluated=True):
        """Return capped monthly stars, demerits, and status for net points."""
        if not evaluated:
            return 0, 0, Employee.STATUS_NOT_EVALUATED
        if points >= 90:
            return 2, 0, Employee.STATUS_EXCELLENT
        if points >= 70:
            return 1, 0, Employee.STATUS_GOOD
        if points >= 0:
            return 0, 0, Employee.STATUS_NO_AWARD
        return 0, 1, Employee.STATUS_NEEDS_IMPROVEMENT

    def recalculate_performance(self, save=True, reference_date=None):
        """Recalculate the live summary from records in one Manila calendar month."""
        from .manila_tz_utils import get_manila_today

        reference_date = reference_date or get_manila_today()
        month_start = reference_date.replace(day=1)
        if month_start.month == 12:
            next_month_start = month_start.replace(year=month_start.year + 1, month=1)
        else:
            next_month_start = month_start.replace(month=month_start.month + 1)

        records = self.performance_records.filter(
            record_date__gte=month_start,
            record_date__lt=next_month_start,
        )
        aggregates = records.aggregate(
            positive=Sum('points', filter=models.Q(record_type=PerformanceRecord.POSITIVE)),
            negative=Sum('points', filter=models.Q(record_type=PerformanceRecord.NEGATIVE)),
        )
        points = int(aggregates['positive'] or 0) - int(aggregates['negative'] or 0)
        stars, demerits, status = self.result_for_points(points, evaluated=records.exists())

        self.monthly_points = points
        self.total_stars = min(stars, 2)
        self.total_demerits = min(demerits, 1)
        self.performance_status = status
        if save:
            Employee.objects.filter(pk=self.pk).update(
                monthly_points=self.monthly_points,
                total_stars=self.total_stars,
                total_demerits=self.total_demerits,
                performance_status=self.performance_status,
                updated_at=timezone.now(),
            )
        return self.monthly_points


    def recalculate_yearly_evaluation_summary(self, save=True, year=None):
        """Synchronize cached totals from monthly evaluations for one year."""
        from .manila_tz_utils import get_manila_today

        selected_year = year or get_manila_today().year
        evaluations = self.monthly_evaluations.filter(year=selected_year)
        totals = evaluations.aggregate(
            stars=Sum('stars'),
            demerits=Sum('demerits'),
        )
        evaluation_count = evaluations.count()

        self.monthly_points = 0
        self.total_stars = min(int(totals['stars'] or 0), 24)
        self.total_demerits = int(totals['demerits'] or 0)
        if evaluation_count == 0:
            self.performance_status = self.STATUS_NOT_EVALUATED
        elif self.total_demerits > 0:
            self.performance_status = self.STATUS_NEEDS_IMPROVEMENT
        elif self.total_stars >= 18:
            self.performance_status = self.STATUS_EXCELLENT
        elif self.total_stars >= 12:
            self.performance_status = self.STATUS_GOOD
        else:
            self.performance_status = self.STATUS_NO_AWARD

        if save:
            Employee.objects.filter(pk=self.pk).update(
                monthly_points=self.monthly_points,
                total_stars=self.total_stars,
                total_demerits=self.total_demerits,
                performance_status=self.performance_status,
                updated_at=timezone.now(),
            )
        return self.total_stars



class EmployeeMonthlyPerformance(models.Model):
    """One retained evaluation per employee, month, and year."""

    MONTH_CHOICES = [(month, month) for month in range(1, 13)]
    STAR_CHOICES = [(0, '0'), (1, '1'), (2, '2')]

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name='monthly_evaluations',
    )
    month = models.PositiveSmallIntegerField(
        choices=MONTH_CHOICES,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
    )
    year = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(2000), MaxValueValidator(2100)],
    )
    stars = models.PositiveSmallIntegerField(
        choices=STAR_CHOICES,
        validators=[MinValueValidator(0), MaxValueValidator(2)],
    )
    demerits = models.PositiveSmallIntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(999)],
    )
    admin_remarks = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'employee_monthly_performance'
        ordering = ['-year', '-month', 'employee__full_name']
        constraints = [
            models.UniqueConstraint(
                fields=['employee', 'month', 'year'],
                name='unique_employee_month_year_evaluation',
            ),
        ]
        indexes = [
            models.Index(
                fields=['employee', 'year'],
                name='emp_monthly_perf_emp_year_idx',
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if not str(self.admin_remarks or '').strip():
            raise ValidationError({'admin_remarks': 'Admin remarks are required.'})

    def save(self, *args, **kwargs):
        self.admin_remarks = str(self.admin_remarks or '').strip()
        self.full_clean()
        previous_employee_id = None
        if self.pk:
            previous_employee_id = type(self).objects.filter(pk=self.pk).values_list(
                'employee_id', flat=True
            ).first()
        result = super().save(*args, **kwargs)
        if previous_employee_id and previous_employee_id != self.employee_id:
            previous_employee = Employee.objects.filter(pk=previous_employee_id).first()
            if previous_employee:
                previous_employee.recalculate_yearly_evaluation_summary()
        self.employee.recalculate_yearly_evaluation_summary()
        return result

    def delete(self, *args, **kwargs):
        employee = self.employee
        result = super().delete(*args, **kwargs)
        employee.recalculate_yearly_evaluation_summary()
        return result

    def __str__(self):
        return f'{self.employee.full_name} - {self.month:02d}/{self.year}'


class PerformanceRecord(models.Model):
    POSITIVE = 'positive'
    NEGATIVE = 'negative'
    RECORD_TYPE_CHOICES = [
        (POSITIVE, 'Positive'),
        (NEGATIVE, 'Negative'),
    ]

    record_id = models.BigAutoField(primary_key=True)
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='performance_records')
    record_type = models.CharField(max_length=10, choices=RECORD_TYPE_CHOICES)
    description = models.TextField()
    points = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(100)])
    record_date = models.DateField(default=timezone.localdate)
    submission_key = models.UUIDField(unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pages_performancerecord'
        ordering = ['-record_date', '-created_at']

    def __str__(self):
        return f'{self.employee.full_name} - {self.get_record_type_display()} ({self.points})'

    @property
    def date(self):
        return self.record_date

    @property
    def type(self):
        return self.get_record_type_display()

    @property
    def signed_points(self):
        return self.points if self.record_type == self.POSITIVE else -self.points

    def save(self, *args, **kwargs):
        from uuid import uuid4
        if not self.submission_key:
            self.submission_key = uuid4()
        old_employee_id = None
        if self.pk:
            old_employee_id = type(self).objects.filter(pk=self.pk).values_list('employee_id', flat=True).first()
        super().save(*args, **kwargs)
        if old_employee_id and old_employee_id != self.employee_id:
            Employee.objects.filter(pk=old_employee_id).first().recalculate_performance()
        self.employee.recalculate_performance()

    def delete(self, *args, **kwargs):
        employee = self.employee
        super().delete(*args, **kwargs)
        employee.recalculate_performance()


class MonthlyPerformanceSummary(models.Model):
    """Permanent month-end verification record for manual annual totaling."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='monthly_performance_summaries')
    month = models.DateField(help_text='First day of the archived Manila month')
    final_points = models.IntegerField(default=0)
    stars = models.PositiveSmallIntegerField(default=0)
    demerits = models.PositiveSmallIntegerField(default=0)
    performance_status = models.CharField(max_length=40)
    archived_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'pages_monthlyperformancesummary'
        ordering = ['-month', 'employee__full_name']
        constraints = [
            models.UniqueConstraint(fields=['employee', 'month'], name='unique_employee_monthly_summary'),
        ]

    def __str__(self):
        return f'{self.employee.full_name} - {self.month:%B %Y}'


class MonthlyCleanupRun(models.Model):
    """Durable idempotency record for the Manila monthly cleanup."""
    month = models.DateField(unique=True, help_text='First day of the month that was cleaned')
    completed_at = models.DateTimeField(auto_now_add=True)
    orders_deleted = models.PositiveIntegerField(default=0)
    performance_records_deleted = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'pages_monthlycleanuprun'
        ordering = ['-month']

    def __str__(self):
        return f'Monthly cleanup: {self.month:%B %Y}'
class EmployeeStandingPin(models.Model):
    """Database-backed, hashed second factor for the Employee Standing module."""

    pin_hash = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='+',
    )

    class Meta:
        db_table = 'pages_employee_standing_pin'
        verbose_name = 'Employee Standing PIN'
        verbose_name_plural = 'Employee Standing PIN'
        constraints = [
            models.UniqueConstraint(
                fields=['is_active'],
                condition=models.Q(is_active=True),
                name='one_active_employee_standing_pin',
            ),
        ]

    def set_pin(self, raw_pin):
        self.pin_hash = make_password(str(raw_pin))

    def check_pin(self, raw_pin):
        return check_password(str(raw_pin), self.pin_hash)

    def __str__(self):
        return 'Active Employee Standing PIN' if self.is_active else 'Inactive Employee Standing PIN'

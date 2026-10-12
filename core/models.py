from decimal import Decimal
from django.db import models
from django.utils import timezone


class Product(models.Model):
    """
    Product in the distributor catalogue.
    SKU is the natural unique primary key.
    Stock represents current available inventory in the distributor warehouse.
    """
    sku = models.CharField(max_length=64, primary_key=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=100, db_index=True)
    brand = models.CharField(max_length=100, db_index=True)
    vehicle_fitment = models.CharField(max_length=255, db_index=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    stock = models.IntegerField(default=0)
    description = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "products"
        ordering = ["sku"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(stock__gte=0),
                name="product_stock_non_negative"
            ),
            models.CheckConstraint(
                check=models.Q(price__gt=Decimal("0.00")),
                name="product_price_positive"
            )
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"


class Dealer(models.Model):
    """
    Registered dealer placing orders with the distributor.
    dealer_code is unique (e.g. DLR-007).
    """
    dealer_code = models.CharField(max_length=64, unique=True, db_index=True)
    name = models.CharField(max_length=255)
    city = models.CharField(max_length=100, blank=True, default="")
    phone = models.CharField(max_length=50, blank=True, default="")
    email = models.CharField(max_length=255, blank=True, default="")
    gstin = models.CharField(max_length=50, blank=True, default="")
    credit_limit = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "dealers"
        ordering = ["id"]

    def __str__(self):
        return f"{self.dealer_code}: {self.name}"


class OrderStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    CONFIRMED = "CONFIRMED", "Confirmed"
    DISPATCHED = "DISPATCHED", "Dispatched"
    DELIVERED = "DELIVERED", "Delivered"
    CANCELLED = "CANCELLED", "Cancelled"


class Order(models.Model):
    """
    Dealer order with lifecycle transitions:
    DRAFT -> CONFIRMED -> DISPATCHED -> DELIVERED
    DRAFT or CONFIRMED -> CANCELLED
    """
    order_number = models.CharField(max_length=64, unique=True, db_index=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(
        max_length=32,
        choices=OrderStatus.choices,
        default=OrderStatus.DRAFT,
        db_index=True
    )
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    grand_total = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    
    # Idempotency tracking
    idempotency_key = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    idempotency_hash = models.CharField(max_length=64, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "orders"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.order_number} ({self.status})"


class OrderItem(models.Model):
    """
    Line item belonging to an Order.
    Takes an immutable snapshot of sku and name upon creation.
    Calculates line GST at 18% with ROUND_HALF_UP.
    """
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    sku = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    line_subtotal = models.DecimalField(max_digits=14, decimal_places=2)
    line_tax = models.DecimalField(max_digits=14, decimal_places=2)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        db_table = "order_items"
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "product"], name="unique_order_product")
        ]

    def __str__(self):
        return f"{self.sku} x {self.quantity} in {self.order.order_number}"


class DailyOrderSequence(models.Model):
    """
    Row-level locked sequence counter for thread-safe ORD-YYYYMMDD-NNNN order numbers.
    """
    date_str = models.CharField(max_length=8, primary_key=True)  # YYYYMMDD in Asia/Kolkata
    last_seq = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "daily_order_sequences"


class AssistantMessage(models.Model):
    """
    Conversation turns for Dealer Assistant session memory.
    """
    session_id = models.CharField(max_length=255, db_index=True)
    dealer = models.ForeignKey(Dealer, on_delete=models.CASCADE, related_name="assistant_messages")
    role = models.CharField(max_length=20)  # user, assistant, system, tool
    content = models.TextField()
    tool_calls = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assistant_messages"
        ordering = ["created_at"]

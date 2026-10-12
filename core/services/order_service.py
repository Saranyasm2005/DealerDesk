from decimal import Decimal
import hashlib
import json
from django.db import transaction, IntegrityError
from django.utils import timezone

from core.models import Product, Dealer, Order, OrderItem, OrderStatus, DailyOrderSequence
from core.utils import calculate_line_amounts


class OrderServiceException(Exception):
    def __init__(self, code: str, message: str, details: list = None, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or []
        self.http_status = http_status


class InsufficientStockException(OrderServiceException):
    def __init__(self, details: list, message: str):
        super().__init__(
            code="INSUFFICIENT_STOCK",
            message=message,
            details=details,
            http_status=409,
        )


class InvalidTransitionException(OrderServiceException):
    def __init__(self, current_status: str, action: str):
        super().__init__(
            code="INVALID_TRANSITION",
            message=f"Cannot perform '{action}' on order with status '{current_status}'.",
            http_status=409,
        )


class OrderLockedException(OrderServiceException):
    def __init__(self, current_status: str):
        super().__init__(
            code="ORDER_LOCKED",
            message=f"Order cannot be modified because status is '{current_status}'.",
            http_status=409,
        )


class IdempotencyConflictException(OrderServiceException):
    def __init__(self):
        super().__init__(
            code="IDEMPOTENCY_CONFLICT",
            message="Idempotency-Key reused with different request payload.",
            http_status=409,
        )


class ValidationException(OrderServiceException):
    def __init__(self, message: str, details: list = None):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            details=details or [],
            http_status=400,
        )


def compute_body_hash(data: dict) -> str:
    """Compute deterministic SHA256 of order request body."""
    normalized = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_order_number() -> str:
    """
    Generate sequential ORD-YYYYMMDD-NNNN unique order number in Asia/Kolkata timezone.
    Uses row-level locking on DailyOrderSequence for atomic increment under concurrency.
    """
    now = timezone.localtime(timezone.now())
    date_str = now.strftime("%Y%m%d")
    
    with transaction.atomic():
        try:
            seq_obj, _ = DailyOrderSequence.objects.select_for_update().get_or_create(date_str=date_str)
        except IntegrityError:
            seq_obj = DailyOrderSequence.objects.select_for_update().get(date_str=date_str)
        seq_obj.last_seq += 1
        seq_obj.save(update_fields=["last_seq"])
        seq_num = seq_obj.last_seq

    return f"ORD-{date_str}-{seq_num:04d}"


def validate_order_items(items_data: list) -> list[dict]:
    """Validate item structure, quantities, unique SKUs, and SKU existence."""
    if not items_data or not isinstance(items_data, list):
        raise ValidationException("Order must have at least one item.")

    seen_skus = set()
    validated = []
    
    for idx, item in enumerate(items_data):
        if not isinstance(item, dict):
            raise ValidationException(f"Item at index {idx} must be a JSON object.")
        
        sku = item.get("sku")
        if not sku or not isinstance(sku, str):
            raise ValidationException(f"Missing or invalid SKU at item index {idx}.")
        
        sku = sku.strip()
        if sku in seen_skus:
            raise ValidationException(f"Duplicate SKU '{sku}' found in order items.")
        seen_skus.add(sku)

        quantity = item.get("quantity")
        if quantity is None or type(quantity) is not int or quantity < 1:
            raise ValidationException(f"Quantity for SKU '{sku}' must be an integer greater than or equal to 1.")

        validated.append({"sku": sku, "quantity": quantity})

    return validated


def create_draft_order(dealer_id: int, items_data: list, idempotency_key: str = None, request_body: dict = None) -> tuple[Order, bool]:
    """
    Create a new DRAFT order with items and calculate 18% GST with ROUND_HALF_UP.
    Handles Idempotency-Key to prevent duplicate orders.
    Returns (Order, was_created: bool).
    """
    body_hash = compute_body_hash(request_body) if request_body is not None else ""

    # Check idempotency first if key provided
    if idempotency_key:
        existing_order = Order.objects.filter(idempotency_key=idempotency_key).first()
        if existing_order:
            if existing_order.idempotency_hash and existing_order.idempotency_hash != body_hash:
                raise IdempotencyConflictException()
            return existing_order, False

    # Validate dealer
    try:
        dealer = Dealer.objects.get(id=dealer_id)
    except (Dealer.DoesNotExist, ValueError, TypeError):
        raise ValidationException(f"Dealer with id '{dealer_id}' does not exist.")

    # Validate items
    validated_items = validate_order_items(items_data)
    skus = [it["sku"] for it in validated_items]

    # Fetch products
    products_by_sku = {p.sku: p for p in Product.objects.filter(sku__in=skus)}
    for sku in skus:
        if sku not in products_by_sku:
            raise ValidationException(f"Product with SKU '{sku}' does not exist.")

    try:
        with transaction.atomic():
            order_number = generate_order_number()
            order = Order.objects.create(
                order_number=order_number,
                dealer=dealer,
                status=OrderStatus.DRAFT,
                idempotency_key=idempotency_key,
                idempotency_hash=body_hash,
            )

            subtotal = Decimal("0.00")
            tax_total = Decimal("0.00")
            order_items = []

            for it in validated_items:
                product = products_by_sku[it["sku"]]
                qty = it["quantity"]
                unit_price = product.price

                line_subtotal, line_tax, line_total = calculate_line_amounts(qty, unit_price)
                subtotal += line_subtotal
                tax_total += line_tax

                order_items.append(
                    OrderItem(
                        order=order,
                        product=product,
                        sku=product.sku,
                        name=product.name,
                        quantity=qty,
                        unit_price=unit_price,
                        line_subtotal=line_subtotal,
                        line_tax=line_tax,
                        line_total=line_total,
                    )
                )

            OrderItem.objects.bulk_create(order_items)

            order.subtotal = subtotal
            order.tax_total = tax_total
            order.grand_total = subtotal + tax_total
            order.save(update_fields=["subtotal", "tax_total", "grand_total"])

        return order, True
    except IntegrityError:
        if idempotency_key:
            existing = Order.objects.filter(idempotency_key=idempotency_key).first()
            if existing:
                if existing.idempotency_hash and existing.idempotency_hash != body_hash:
                    raise IdempotencyConflictException()
                return existing, False
        raise


def update_draft_order(order: Order, items_data: list) -> Order:
    """
    Replace all items in a DRAFT order. Raises ORDER_LOCKED if not DRAFT.
    """
    if order.status != OrderStatus.DRAFT:
        raise OrderLockedException(order.status)

    validated_items = validate_order_items(items_data)
    skus = [it["sku"] for it in validated_items]
    products_by_sku = {p.sku: p for p in Product.objects.filter(sku__in=skus)}
    for sku in skus:
        if sku not in products_by_sku:
            raise ValidationException(f"Product with SKU '{sku}' does not exist.")

    with transaction.atomic():
        order.items.all().delete()

        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        order_items = []

        for it in validated_items:
            product = products_by_sku[it["sku"]]
            qty = it["quantity"]
            unit_price = product.price

            line_subtotal, line_tax, line_total = calculate_line_amounts(qty, unit_price)
            subtotal += line_subtotal
            tax_total += line_tax

            order_items.append(
                OrderItem(
                    order=order,
                    product=product,
                    sku=product.sku,
                    name=product.name,
                    quantity=qty,
                    unit_price=unit_price,
                    line_subtotal=line_subtotal,
                    line_tax=line_tax,
                    line_total=line_total,
                )
            )

        OrderItem.objects.bulk_create(order_items)
        order.subtotal = subtotal
        order.tax_total = tax_total
        order.grand_total = subtotal + tax_total
        order.save(update_fields=["subtotal", "tax_total", "grand_total", "updated_at"])

    return order


def confirm_order(order: Order) -> Order:
    """
    Transition DRAFT -> CONFIRMED.
    All-or-nothing stock validation and deduction with select_for_update row locking.
    Locks item prices at confirmation.
    """
    if order.status != OrderStatus.DRAFT:
        raise InvalidTransitionException(order.status, "confirm")

    with transaction.atomic():
        # Lock products ordered by SKU to avoid deadlocks
        items = list(order.items.select_related("product").order_by("sku"))
        skus = [it.sku for it in items]
        locked_products = {
            p.sku: p
            for p in Product.objects.select_for_update().filter(sku__in=skus).order_by("sku")
        }

        short_lines = []
        sentences = []
        for it in items:
            p = locked_products.get(it.sku)
            if not p or it.quantity > p.stock:
                avail = p.stock if p else 0
                short_lines.append({
                    "sku": it.sku,
                    "requested": it.quantity,
                    "available": avail,
                })
                p_name = p.name if p else it.name
                sentences.append(f"Insufficient stock for {p_name}. Available: {avail}, Requested: {it.quantity}")

        if short_lines:
            raise InsufficientStockException(details=short_lines, message=". ".join(sentences))

        # Deduct stock and lock item prices at confirmation time
        subtotal = Decimal("0.00")
        tax_total = Decimal("0.00")
        for it in items:
            p = locked_products[it.sku]
            p.stock -= it.quantity
            p.save(update_fields=["stock", "updated_at"])

            it.unit_price = p.price
            it.line_subtotal, it.line_tax, it.line_total = calculate_line_amounts(it.quantity, it.unit_price)
            it.save(update_fields=["unit_price", "line_subtotal", "line_tax", "line_total"])
            subtotal += it.line_subtotal
            tax_total += it.line_tax

        order.subtotal = subtotal
        order.tax_total = tax_total
        order.grand_total = subtotal + tax_total
        order.status = OrderStatus.CONFIRMED
        order.save(update_fields=["status", "subtotal", "tax_total", "grand_total", "updated_at"])

    return order


def cancel_order(order: Order) -> Order:
    """
    Transition DRAFT -> CANCELLED or CONFIRMED -> CANCELLED.
    Restores stock exactly once if order was CONFIRMED.
    """
    if order.status not in (OrderStatus.DRAFT, OrderStatus.CONFIRMED):
        raise InvalidTransitionException(order.status, "cancel")

    with transaction.atomic():
        if order.status == OrderStatus.CONFIRMED:
            items = list(order.items.order_by("sku"))
            skus = [it.sku for it in items]
            locked_products = {
                p.sku: p
                for p in Product.objects.select_for_update().filter(sku__in=skus).order_by("sku")
            }
            for it in items:
                p = locked_products.get(it.sku)
                if p:
                    p.stock += it.quantity
                    p.save(update_fields=["stock", "updated_at"])

        order.status = OrderStatus.CANCELLED
        order.save(update_fields=["status", "updated_at"])

    return order


def dispatch_order(order: Order) -> Order:
    """Transition CONFIRMED -> DISPATCHED."""
    if order.status != OrderStatus.CONFIRMED:
        raise InvalidTransitionException(order.status, "dispatch")
    order.status = OrderStatus.DISPATCHED
    order.save(update_fields=["status", "updated_at"])
    return order


def deliver_order(order: Order) -> Order:
    """Transition DISPATCHED -> DELIVERED."""
    if order.status != OrderStatus.DISPATCHED:
        raise InvalidTransitionException(order.status, "deliver")
    order.status = OrderStatus.DELIVERED
    order.save(update_fields=["status", "updated_at"])
    return order

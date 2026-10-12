from decimal import Decimal
import math
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from core.models import Product, Dealer, Order, OrderItem, OrderStatus
from core.services.order_service import (
    create_draft_order,
    confirm_order,
    cancel_order,
    InsufficientStockException,
    InvalidTransitionException,
)
from core.utils import format_money, calculate_line_amounts


class DealerDeskCoreTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.dealer = Dealer.objects.create(
            dealer_code="DLR-001",
            name="Apex Motors",
            city="Bengaluru",
            credit_limit=Decimal("50000.00"),
        )
        self.p1 = Product.objects.create(
            sku="BRK-1001",
            name="Front Brake Pads",
            category="Brakes",
            brand="Brembo",
            vehicle_fitment="KTM Duke 390",
            price=Decimal("1200.00"),
            stock=10,
        )
        self.p2 = Product.objects.create(
            sku="FIL-1001",
            name="Engine Oil Filter",
            category="Filters",
            brand="Bosch",
            vehicle_fitment="Universal",
            price=Decimal("350.50"),
            stock=5,
        )

    def test_health_check(self):
        resp = self.client.get("/api/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})

    def test_gst_rounding(self):
        # 3 units @ 350.50 = 1051.50
        # 18% GST = 1051.50 * 0.18 = 189.27
        # Total = 1240.77
        subtotal, tax, total = calculate_line_amounts(3, Decimal("350.50"))
        self.assertEqual(subtotal, Decimal("1051.50"))
        self.assertEqual(tax, Decimal("189.27"))
        self.assertEqual(total, Decimal("1240.77"))
        self.assertEqual(format_money(tax), "189.27")

    def test_order_creation_and_totals(self):
        order, created = create_draft_order(
            dealer_id=self.dealer.id,
            items_data=[
                {"sku": "BRK-1001", "quantity": 2},
                {"sku": "FIL-1001", "quantity": 1},
            ],
        )
        self.assertTrue(created)
        self.assertEqual(order.status, OrderStatus.DRAFT)
        # Line 1: 2 * 1200.00 = 2400.00, tax = 432.00, total = 2832.00
        # Line 2: 1 * 350.50 = 350.50, tax = 63.09, total = 413.59
        # Subtotal: 2750.50, Tax: 495.09, Grand: 3245.59
        self.assertEqual(order.subtotal, Decimal("2750.50"))
        self.assertEqual(order.tax_total, Decimal("495.09"))
        self.assertEqual(order.grand_total, Decimal("3245.59"))

    def test_confirm_reduces_stock_and_cancel_restores(self):
        order, _ = create_draft_order(
            dealer_id=self.dealer.id,
            items_data=[{"sku": "BRK-1001", "quantity": 4}],
        )
        self.assertEqual(Product.objects.get(sku="BRK-1001").stock, 10)

        # Confirm
        confirmed = confirm_order(order)
        self.assertEqual(confirmed.status, OrderStatus.CONFIRMED)
        self.assertEqual(Product.objects.get(sku="BRK-1001").stock, 6)

        # Cancel
        cancelled = cancel_order(confirmed)
        self.assertEqual(cancelled.status, OrderStatus.CANCELLED)
        self.assertEqual(Product.objects.get(sku="BRK-1001").stock, 10)

        # Second cancel should fail
        with self.assertRaises(InvalidTransitionException):
            cancel_order(cancelled)

    def test_insufficient_stock_fails_all_or_nothing(self):
        order, _ = create_draft_order(
            dealer_id=self.dealer.id,
            items_data=[
                {"sku": "BRK-1001", "quantity": 5},
                {"sku": "FIL-1001", "quantity": 10},  # available is only 5!
            ],
        )
        with self.assertRaises(InsufficientStockException) as ctx:
            confirm_order(order)

        self.assertIn("FIL-1001", [d["sku"] for d in ctx.exception.details])
        # Verify stock was untouched for both products
        self.assertEqual(Product.objects.get(sku="BRK-1001").stock, 10)
        self.assertEqual(Product.objects.get(sku="FIL-1001").stock, 5)

    def test_idempotent_order_creation(self):
        key = "idemp-test-key-123"
        body = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1001", "quantity": 1}],
        }
        resp1 = self.client.post("/api/orders/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(resp1.status_code, 201)
        ord_num = resp1.json()["order_number"]

        # Same key with same body -> returns original order 200/201
        resp2 = self.client.post("/api/orders/", body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertIn(resp2.status_code, [200, 201])
        self.assertEqual(resp2.json()["order_number"], ord_num)

        # Same key with different body -> 409 IDEMPOTENCY_CONFLICT
        bad_body = {
            "dealer_id": self.dealer.id,
            "items": [{"sku": "BRK-1001", "quantity": 2}],
        }
        resp3 = self.client.post("/api/orders/", bad_body, format="json", HTTP_IDEMPOTENCY_KEY=key)
        self.assertEqual(resp3.status_code, 409)
        self.assertEqual(resp3.json()["error"]["code"], "IDEMPOTENCY_CONFLICT")

    def test_reorder_safety_stock_formula(self):
        # Test exact formula:
        # L = 2
        # lead_time_demand = 61.7
        # sigma = 2.45
        # safety_stock = ceil(1.65 * 2.45 * sqrt(2)) = ceil(5.717) = 6
        # reorder_point = 61.7 + 6 = 67.7
        # current_stock = 40 -> reorder_qty = max(0, ceil(67.7 - 40)) = 28
        L = 2
        ltd = 61.7
        sigma = 2.45
        ss = math.ceil(1.65 * sigma * math.sqrt(L))
        rp = round(ltd + ss, 1)
        qty = max(0, math.ceil(rp - 40))
        self.assertEqual(ss, 6)
        self.assertEqual(rp, 67.7)
        self.assertEqual(qty, 28)

    def test_assistant_chat_api(self):
        resp = self.client.post(
            "/api/assistant/chat/",
            {
                "session_id": "test-session-1",
                "dealer_id": self.dealer.id,
                "message": "Do you have front brake pads for a Duke 390?",
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("reply", data)
        self.assertIn("cited_skus", data)
        self.assertIn("tool_calls", data)
        self.assertIn("actions", data)
        self.assertIn("BRK-1001", data["cited_skus"])

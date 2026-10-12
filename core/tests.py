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
        self.assertIn("BRK-1001", data["cited_skus"])

    def test_inventory_validation(self):
        # Quantity must not be boolean or negative
        resp = self.client.put(
            "/api/inventory/BRK-1001/",
            {"quantity": True, "reason": "invalid"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "VALIDATION_ERROR")

        resp2 = self.client.put(
            "/api/inventory/BRK-1001/",
            {"quantity": -5, "reason": "negative"},
            format="json",
        )
        self.assertEqual(resp2.status_code, 400)
        self.assertEqual(resp2.json()["error"]["code"], "VALIDATION_ERROR")

    def test_draft_order_price_tracking_and_confirmation_lock(self):
        # 1. Create draft order with p1 at 1200.00
        order, _ = create_draft_order(
            dealer_id=self.dealer.id,
            items_data=[{"sku": "BRK-1001", "quantity": 1}],
        )
        self.assertEqual(order.items.first().unit_price, Decimal("1200.00"))

        # 2. Change product price to 1500.00 while DRAFT
        self.p1.price = Decimal("1500.00")
        self.p1.save()

        # 3. GET /api/orders/{order_number}/ reflects current catalogue price
        resp = self.client.get(f"/api/orders/{order.order_number}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["items"][0]["unit_price"], "1500.00")
        self.assertEqual(resp.json()["subtotal"], "1500.00")

        # 4. Confirm order locks the price at 1500.00
        confirm_resp = self.client.post(f"/api/orders/{order.order_number}/confirm/")
        self.assertEqual(confirm_resp.status_code, 200)
        self.assertEqual(confirm_resp.json()["status"], "CONFIRMED")
        self.assertEqual(confirm_resp.json()["items"][0]["unit_price"], "1500.00")

        # 5. Later product price change to 2000.00 has ZERO effect on confirmed order
        self.p1.price = Decimal("2000.00")
        self.p1.save()

        resp_after = self.client.get(f"/api/orders/{order.order_number}/")
        self.assertEqual(resp_after.status_code, 200)
        self.assertEqual(resp_after.json()["items"][0]["unit_price"], "1500.00")
        self.assertEqual(resp_after.json()["subtotal"], "1500.00")

    def test_order_invalid_transitions(self):
        order, _ = create_draft_order(
            dealer_id=self.dealer.id,
            items_data=[{"sku": "BRK-1001", "quantity": 1}],
        )
        # Cannot dispatch a DRAFT
        resp = self.client.post(f"/api/orders/{order.order_number}/dispatch/")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "INVALID_TRANSITION")

        # Cannot deliver a DRAFT
        resp = self.client.post(f"/api/orders/{order.order_number}/deliver/")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "INVALID_TRANSITION")

    def test_reorder_formula_edge_cases(self):
        # Case 1: Zero stock
        # LTD = 50.0, sigma = 10.0, L = 1 -> SS = ceil(1.65 * 10 * 1) = 17
        # ROP = 50.0 + 17 = 67.0, Stock = 0 -> Qty = 67
        L1 = 1
        ltd1 = 50.0
        sigma1 = 10.0
        ss1 = math.ceil(1.65 * sigma1 * math.sqrt(L1))
        rop1 = round(ltd1 + ss1, 1)
        qty1 = max(0, math.ceil(rop1 - 0))
        self.assertEqual(ss1, 17)
        self.assertEqual(rop1, 67.0)
        self.assertEqual(qty1, 67)

        # Case 2: Stock exceeds reorder point (no reorder needed)
        qty2 = max(0, math.ceil(rop1 - 100))
        self.assertEqual(qty2, 0)

        # Case 3: Zero variance / stable sales (sigma = 0)
        ss3 = math.ceil(1.65 * 0.0 * math.sqrt(4))
        self.assertEqual(ss3, 0)
        rop3 = round(40.0 + ss3, 1)
        self.assertEqual(rop3, 40.0)

        # Case 4: Long lead time (L = 4)
        # LTD = 200.0, sigma = 8.5, L = 4 -> SS = ceil(1.65 * 8.5 * 2) = ceil(28.05) = 29
        ss4 = math.ceil(1.65 * 8.5 * math.sqrt(4))
        self.assertEqual(ss4, 29)
        rop4 = round(200.0 + ss4, 1)
        self.assertEqual(rop4, 229.0)

    def test_assistant_search_and_ambiguity(self):
        from core.services.assistant_service import execute_search_parts, chat_turn

        # Test search by exact SKU
        res_sku = execute_search_parts(query="BRK-1001")
        self.assertTrue(len(res_sku) >= 1)
        self.assertEqual(res_sku[0]["sku"], "BRK-1001")

        # Test search by category and vehicle
        res_cat = execute_search_parts(query="pads", vehicle="KTM Duke 390", category="Brakes")
        self.assertTrue(len(res_cat) >= 1)
        self.assertEqual(res_cat[0]["sku"], "BRK-1001")

        # Test clarifying question for ambiguous request
        turn = chat_turn("sess-ambig-1", self.dealer.id, "Do you have brake pads?")
        self.assertIn("Which vehicle make and model", turn["reply"])
        self.assertEqual(len(turn["actions"]), 0)

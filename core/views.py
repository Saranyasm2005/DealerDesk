import csv
import math
import os
from pathlib import Path
import re
import statistics
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Product, Dealer, Order, OrderStatus
from core.serializers import (
    ProductSerializer,
    ProductCreateSerializer,
    DealerSerializer,
    DealerCreateSerializer,
    OrderSerializer,
)
from core.services.order_service import (
    create_draft_order,
    update_draft_order,
    confirm_order,
    cancel_order,
    dispatch_order,
    deliver_order,
    OrderServiceException,
)
from core.services.supplier_sync import run_supplier_sync, SupplierSyncException
from core.services.assistant_service import chat_turn
from core.utils import error_response


from django.shortcuts import render

class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class PortalView(APIView):
    def get(self, request):
        return render(request, "portal.html")


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok"})


# ---- Products -------------------------------------------------------------------------

class ProductListCreateView(APIView):
    pagination_class = StandardPagination

    def get(self, request):
        qs = Product.objects.all()

        search = request.query_params.get("search")
        if search:
            search = search.strip()
            qs = qs.filter(
                Q(sku__icontains=search)
                | Q(name__icontains=search)
                | Q(brand__icontains=search)
            )

        category = request.query_params.get("category")
        if category:
            qs = qs.filter(category=category.strip())

        vehicle = request.query_params.get("vehicle")
        if vehicle:
            qs = qs.filter(vehicle_fitment__icontains=vehicle.strip())

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = ProductSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        data = request.data.copy()
        sku = data.get("sku")
        if not sku:
            return error_response("VALIDATION_ERROR", "Field 'sku' is required.", status=400)

        sku = sku.strip()
        if Product.objects.filter(sku=sku).exists():
            return error_response("DUPLICATE", f"Product with SKU '{sku}' already exists.", status=409)

        serializer = ProductCreateSerializer(data=data)
        if not serializer.is_valid():
            err_msg = "; ".join(f"{k}: {', '.join(v)}" for k, v in serializer.errors.items())
            return error_response("VALIDATION_ERROR", err_msg, details=list(serializer.errors.keys()), status=400)

        product = serializer.save(stock=0)
        return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)


class ProductDetailView(APIView):
    def get(self, request, sku):
        try:
            product = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            return error_response("NOT_FOUND", f"Product with SKU '{sku}' not found.", status=404)
        return Response(ProductSerializer(product).data)

    def patch(self, request, sku):
        try:
            product = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            return error_response("NOT_FOUND", f"Product with SKU '{sku}' not found.", status=404)

        data = request.data
        if "sku" in data and data["sku"] != sku:
            return error_response("VALIDATION_ERROR", "SKU cannot be changed.", status=400)

        if "price" in data:
            price_str = str(data["price"]).strip()
            if not re.match(r"^\d+(\.\d{1,2})?$", price_str):
                return error_response("VALIDATION_ERROR", "Price must have at most 2 decimal places.", status=400)
            val = Decimal(price_str)
            if val <= Decimal("0.00"):
                return error_response("VALIDATION_ERROR", "Price must be greater than zero.", status=400)
            product.price = val

        for field in ["name", "category", "brand", "vehicle_fitment", "description"]:
            if field in data:
                setattr(product, field, str(data[field]))

        product.save()
        return Response(ProductSerializer(product).data)


# ---- Inventory ------------------------------------------------------------------------

class InventoryUpdateView(APIView):
    def put(self, request, sku):
        try:
            product = Product.objects.get(sku=sku)
        except Product.DoesNotExist:
            return error_response("NOT_FOUND", f"Product with SKU '{sku}' not found.", status=404)

        quantity = request.data.get("quantity")
        if quantity is None or type(quantity) is not int or quantity < 0:
            return error_response("VALIDATION_ERROR", "Quantity must be an integer greater than or equal to 0.", status=400)

        product.stock = quantity
        product.save(update_fields=["stock", "updated_at"])
        return Response({"sku": product.sku, "quantity": product.stock})


# ---- Dealers --------------------------------------------------------------------------

class DealerListCreateView(APIView):
    pagination_class = StandardPagination

    def get(self, request):
        qs = Dealer.objects.all()
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = DealerSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        dealer_code = request.data.get("dealer_code")
        if not dealer_code:
            return error_response("VALIDATION_ERROR", "dealer_code is required.", status=400)

        dealer_code = dealer_code.strip()
        if Dealer.objects.filter(dealer_code=dealer_code).exists():
            return error_response("DUPLICATE", f"Dealer with code '{dealer_code}' already exists.", status=409)

        serializer = DealerCreateSerializer(data=request.data)
        if not serializer.is_valid():
            err_msg = "; ".join(f"{k}: {', '.join(v)}" for k, v in serializer.errors.items())
            return error_response("VALIDATION_ERROR", err_msg, details=list(serializer.errors.keys()), status=400)

        dealer = serializer.save()
        return Response(DealerSerializer(dealer).data, status=status.HTTP_201_CREATED)


class DealerDetailView(APIView):
    def get(self, request, pk):
        try:
            dealer = Dealer.objects.get(pk=pk)
        except Dealer.DoesNotExist:
            return error_response("NOT_FOUND", f"Dealer with ID '{pk}' not found.", status=404)
        return Response(DealerSerializer(dealer).data)


# ---- Orders ---------------------------------------------------------------------------

class OrderListCreateView(APIView):
    pagination_class = StandardPagination

    def get(self, request):
        qs = Order.objects.prefetch_related("items").all()

        dealer_id = request.query_params.get("dealer_id")
        if dealer_id:
            qs = qs.filter(dealer_id=dealer_id)

        status_param = request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param.strip().upper())

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(qs, request)
        serializer = OrderSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)

    def post(self, request):
        dealer_id = request.data.get("dealer_id")
        items = request.data.get("items")
        idempotency_key = request.headers.get("Idempotency-Key")

        if not dealer_id:
            return error_response("VALIDATION_ERROR", "dealer_id is required.", status=400)
        if items is None or not isinstance(items, list):
            return error_response("VALIDATION_ERROR", "items must be a non-empty list.", status=400)

        try:
            order, created = create_draft_order(
                dealer_id=dealer_id,
                items_data=items,
                idempotency_key=idempotency_key,
                request_body=request.data,
            )
            resp_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
            return Response(OrderSerializer(order).data, status=resp_status)
        except OrderServiceException as e:
            return error_response(e.code, e.message, details=e.details, status=e.http_status)


class OrderDetailView(APIView):
    def get(self, request, order_number):
        try:
            order = Order.objects.prefetch_related("items__product").get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found.", status=404)

        if order.status == OrderStatus.DRAFT:
            from core.utils import calculate_line_amounts
            changed = False
            subtotal = Decimal("0.00")
            tax_total = Decimal("0.00")
            for it in order.items.all():
                if it.unit_price != it.product.price:
                    it.unit_price = it.product.price
                    it.line_subtotal, it.line_tax, it.line_total = calculate_line_amounts(it.quantity, it.unit_price)
                    it.save(update_fields=["unit_price", "line_subtotal", "line_tax", "line_total"])
                    changed = True
                subtotal += it.line_subtotal
                tax_total += it.line_tax
            if changed:
                order.subtotal = subtotal
                order.tax_total = tax_total
                order.grand_total = subtotal + tax_total
                order.save(update_fields=["subtotal", "tax_total", "grand_total", "updated_at"])

        return Response(OrderSerializer(order).data)

    def put(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found.", status=404)

        items = request.data.get("items")
        if items is None or not isinstance(items, list):
            return error_response("VALIDATION_ERROR", "items must be a non-empty list.", status=400)

        try:
            updated_order = update_draft_order(order, items)
            return Response(OrderSerializer(updated_order).data)
        except OrderServiceException as e:
            return error_response(e.code, e.message, details=e.details, status=e.http_status)


class OrderConfirmView(APIView):
    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found.", status=404)

        try:
            confirmed = confirm_order(order)
            return Response(OrderSerializer(confirmed).data)
        except OrderServiceException as e:
            return error_response(e.code, e.message, details=e.details, status=e.http_status)


class OrderDispatchView(APIView):
    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found.", status=404)

        try:
            dispatched = dispatch_order(order)
            return Response(OrderSerializer(dispatched).data)
        except OrderServiceException as e:
            return error_response(e.code, e.message, details=e.details, status=e.http_status)


class OrderDeliverView(APIView):
    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found.", status=404)

        try:
            delivered = deliver_order(order)
            return Response(OrderSerializer(delivered).data)
        except OrderServiceException as e:
            return error_response(e.code, e.message, details=e.details, status=e.http_status)


class OrderCancelView(APIView):
    def post(self, request, order_number):
        try:
            order = Order.objects.get(order_number=order_number)
        except Order.DoesNotExist:
            return error_response("NOT_FOUND", f"Order '{order_number}' not found.", status=404)

        try:
            cancelled = cancel_order(order)
            return Response(OrderSerializer(cancelled).data)
        except OrderServiceException as e:
            return error_response(e.code, e.message, details=e.details, status=e.http_status)


# ---- Supplier Sync --------------------------------------------------------------------

class SupplierSyncView(APIView):
    def post(self, request):
        try:
            result = run_supplier_sync()
            return Response(result)
        except SupplierSyncException as e:
            return error_response("SUPPLIER_UNAVAILABLE", str(e), status=502)
        except Exception as e:
            return error_response("SUPPLIER_UNAVAILABLE", f"Sync failed: {e}", status=502)


# ---- Dealer Assistant (GenAI) ---------------------------------------------------------

class AssistantChatView(APIView):
    def post(self, request):
        session_id = request.data.get("session_id")
        dealer_id = request.data.get("dealer_id")
        message = request.data.get("message")

        if not session_id or not isinstance(session_id, str):
            return error_response("VALIDATION_ERROR", "session_id is required.", status=400)
        if dealer_id is None or not isinstance(dealer_id, int):
            return error_response("VALIDATION_ERROR", "dealer_id is required and must be an integer.", status=400)
        if not message or not isinstance(message, str):
            return error_response("VALIDATION_ERROR", "message is required.", status=400)

        response_data = chat_turn(session_id=session_id.strip(), dealer_id=dealer_id, message=message.strip())
        return Response(response_data)


# ---- Forecasting & Reorder (ML) -------------------------------------------------------

class ForecastDetailView(APIView):
    def get(self, request, sku):
        forecast_file = Path("ml") / "forecast.csv"
        if not forecast_file.exists():
            return error_response("NOT_FOUND", "Forecast file not found.", status=404)

        sku_upper = sku.strip().upper()
        matched = []
        with open(forecast_file, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row["sku"].strip().upper() == sku_upper:
                    matched.append({
                        "week_start": row["week_start"].strip(),
                        "forecast_units": float(row["forecast_units"]),
                    })

        if not matched:
            return error_response("NOT_FOUND", f"SKU '{sku}' is not a forecast SKU.", status=404)

        return Response({
            "sku": sku_upper,
            "forecast": matched,
        })


class ReorderSuggestionsView(APIView):
    def get(self, request):
        data_dir = getattr(settings, "DATA_DIR", Path("data"))
        lead_file = data_dir / "lead_times.csv"
        sales_file = data_dir / "sales_history.csv"
        forecast_file = Path("ml") / "forecast.csv"

        if not lead_file.exists() or not sales_file.exists() or not forecast_file.exists():
            return error_response("NOT_FOUND", "Required forecasting or data files not found.", status=404)

        # Read lead times
        lead_times = {}
        with open(lead_file, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                lead_times[r["sku"].strip()] = int(r["lead_time_weeks"])

        # Read forecast units
        forecast_by_sku: dict[str, list[dict]] = {s: [] for s in lead_times}
        with open(forecast_file, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                s = r["sku"].strip()
                if s in forecast_by_sku:
                    forecast_by_sku[s].append({
                        "week_start": r["week_start"].strip(),
                        "units": float(r["forecast_units"]),
                    })

        # Ensure forecast weeks are ordered
        for s in forecast_by_sku:
            forecast_by_sku[s].sort(key=lambda x: x["week_start"])

        # Read sales history for sigma calculation (last 12 weeks per SKU)
        sales_by_sku: dict[str, list[dict]] = {s: [] for s in lead_times}
        with open(sales_file, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                s = r["sku"].strip()
                if s in sales_by_sku:
                    sales_by_sku[s].append({
                        "week_start": r["week_start"].strip(),
                        "units_sold": float(r["units_sold"]),
                    })

        # Get current live stock from Product table
        live_stock_map = {
            p.sku: p.stock
            for p in Product.objects.filter(sku__in=list(lead_times.keys()))
        }

        results = []
        for sku, L in lead_times.items():
            # 1. lead_time_demand = sum of forecast_units for the first L forecast weeks
            sku_forecast = forecast_by_sku.get(sku, [])
            first_l_weeks = sku_forecast[:L]
            lead_time_demand = round(sum(w["units"] for w in first_l_weeks), 1)

            # 2. sigma = sample standard deviation (n - 1) of units_sold over most recent 12 weeks
            sku_sales = sorted(sales_by_sku.get(sku, []), key=lambda x: x["week_start"])
            recent_12 = [x["units_sold"] for x in sku_sales[-12:]]

            if len(recent_12) > 1:
                sigma = statistics.stdev(recent_12)
            else:
                sigma = 0.0

            # 3. safety_stock = ceil(1.65 * sigma * sqrt(L))
            safety_stock = math.ceil(1.65 * sigma * math.sqrt(L))

            # 4. reorder_point = lead_time_demand + safety_stock
            reorder_point = round(lead_time_demand + safety_stock, 1)

            # 5. current_stock = live stock from DB
            current_stock = live_stock_map.get(sku, 0)

            # 6. reorder_qty = max(0, ceil(reorder_point - current_stock))
            reorder_qty = max(0, math.ceil(reorder_point - current_stock))

            results.append({
                "sku": sku,
                "current_stock": current_stock,
                "lead_time_weeks": L,
                "lead_time_demand": lead_time_demand,
                "safety_stock": safety_stock,
                "reorder_point": reorder_point,
                "reorder_qty": reorder_qty,
            })

        return Response({"results": results})

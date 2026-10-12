from django.urls import path

from core.views import (
    HealthView,
    ProductListCreateView,
    ProductDetailView,
    InventoryUpdateView,
    DealerListCreateView,
    DealerDetailView,
    OrderListCreateView,
    OrderDetailView,
    OrderConfirmView,
    OrderDispatchView,
    OrderDeliverView,
    OrderCancelView,
    SupplierSyncView,
    AssistantChatView,
    ForecastDetailView,
    ReorderSuggestionsView,
)

urlpatterns = [
    # Health
    path("health/", HealthView.as_view(), name="health"),
    # Products
    path("products/", ProductListCreateView.as_view(), name="product-list-create"),
    path("products/<str:sku>/", ProductDetailView.as_view(), name="product-detail"),
    # Inventory
    path("inventory/<str:sku>/", InventoryUpdateView.as_view(), name="inventory-update"),
    # Dealers
    path("dealers/", DealerListCreateView.as_view(), name="dealer-list-create"),
    path("dealers/<int:pk>/", DealerDetailView.as_view(), name="dealer-detail"),
    # Orders
    path("orders/", OrderListCreateView.as_view(), name="order-list-create"),
    path("orders/<str:order_number>/", OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:order_number>/confirm/", OrderConfirmView.as_view(), name="order-confirm"),
    path("orders/<str:order_number>/dispatch/", OrderDispatchView.as_view(), name="order-dispatch"),
    path("orders/<str:order_number>/deliver/", OrderDeliverView.as_view(), name="order-deliver"),
    path("orders/<str:order_number>/cancel/", OrderCancelView.as_view(), name="order-cancel"),
    # Supplier Sync
    path("sync/supplier/", SupplierSyncView.as_view(), name="supplier-sync"),
    # Dealer Assistant
    path("assistant/chat/", AssistantChatView.as_view(), name="assistant-chat"),
    # Forecasting & Reorder
    path("forecast/<str:sku>/", ForecastDetailView.as_view(), name="forecast-detail"),
    path("reorder-suggestions/", ReorderSuggestionsView.as_view(), name="reorder-suggestions"),
]

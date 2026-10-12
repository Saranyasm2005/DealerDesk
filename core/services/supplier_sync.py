from decimal import Decimal
import logging
import re
import time
from django.conf import settings
from django.db import transaction
import requests

from core.models import Product


logger = logging.getLogger(__name__)

PRICE_REGEX = re.compile(r"^\d+(\.\d{1,2})?$")


class SupplierSyncException(Exception):
    pass


def validate_supplier_item(item: dict) -> tuple[bool, str]:
    """
    Validate supplier item fields.
    Returns (is_valid, reason).
    """
    sku = item.get("sku")
    if not sku or not isinstance(sku, str) or not sku.strip():
        return False, "missing or empty sku"

    price_str = str(item.get("price", "")).strip()
    if not PRICE_REGEX.match(price_str):
        return False, "invalid price"

    try:
        price_val = Decimal(price_str)
        if price_val <= Decimal("0.00"):
            return False, "invalid price"
    except Exception:
        return False, "invalid price"

    return True, ""


def run_supplier_sync() -> dict:
    """
    Execute full supplier catalogue sync.
    Paginates, retries with backoff / Retry-After, deduplicates by latest updated_at,
    and applies changes atomically without modifying distributor stock.
    """
    api_url = getattr(settings, "SUPPLIER_API_URL", "http://127.0.0.1:8099").rstrip("/")
    api_key = getattr(settings, "SUPPLIER_API_KEY", "")

    cursor = None
    all_raw_items = []
    rejected = []
    pages_fetched = 0
    requests_made = 0

    session = requests.Session()

    while True:
        url = f"{api_url}/v1/products"
        params = {"limit": 20}
        if cursor:
            params["cursor"] = cursor

        headers = {"X-Api-Key": api_key}
        success = False
        last_error = None

        for attempt in range(1, 6):
            requests_made += 1
            try:
                resp = session.get(url, params=params, headers=headers, timeout=20)
                if resp.status_code == 200:
                    data = resp.json()
                    pages_fetched += 1
                    items = data.get("items", [])
                    all_raw_items.extend(items)
                    cursor = data.get("next_cursor")
                    success = True
                    break
                elif resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 1))
                    time.sleep(retry_after)
                elif resp.status_code in (401, 403):
                    raise SupplierSyncException(f"Supplier API authentication failed ({resp.status_code})")
                elif 500 <= resp.status_code < 600:
                    time.sleep(min(0.2 * (2 ** (attempt - 1)), 2.0))
                else:
                    last_error = f"Unexpected status {resp.status_code}"
                    time.sleep(0.2)
            except requests.RequestException as e:
                last_error = str(e)
                time.sleep(min(0.2 * (2 ** (attempt - 1)), 2.0))

        if not success:
            raise SupplierSyncException(f"Failed to fetch supplier catalogue after retries: {last_error}")

        if not cursor:
            break

    # Process items: validate and collect rejected
    valid_items = []
    for item in all_raw_items:
        is_valid, reason = validate_supplier_item(item)
        if not is_valid:
            rejected.append({
                "supplier_sku": item.get("supplier_sku", ""),
                "reason": reason,
            })
        else:
            valid_items.append(item)

    # Deduplicate by SKU using latest updated_at
    latest_by_sku: dict[str, dict] = {}
    for item in valid_items:
        sku = item["sku"].strip()
        curr_updated = item.get("updated_at", "")
        if sku not in latest_by_sku:
            latest_by_sku[sku] = item
        else:
            prev_updated = latest_by_sku[sku].get("updated_at", "")
            if curr_updated > prev_updated:
                latest_by_sku[sku] = item

    created = 0
    updated = 0
    unchanged = 0

    with transaction.atomic():
        skus = list(latest_by_sku.keys())
        existing_products = {p.sku: p for p in Product.objects.filter(sku__in=skus)}

        to_create = []
        to_update = []

        for sku, item in latest_by_sku.items():
            name = item.get("name", "").strip()
            category = item.get("category", "").strip()
            brand = item.get("brand", "").strip()
            vehicle_fitment = item.get("vehicle_fitment", "").strip()
            description = item.get("description", "").strip()
            price = Decimal(str(item["price"]).strip())

            if sku not in existing_products:
                to_create.append(
                    Product(
                        sku=sku,
                        name=name,
                        category=category,
                        brand=brand,
                        vehicle_fitment=vehicle_fitment,
                        description=description,
                        price=price,
                        stock=0,  # Never change warehouse stock
                    )
                )
                created += 1
            else:
                p = existing_products[sku]
                has_diff = (
                    p.name != name
                    or p.category != category
                    or p.brand != brand
                    or p.vehicle_fitment != vehicle_fitment
                    or p.description != description
                    or p.price != price
                )
                if has_diff:
                    p.name = name
                    p.category = category
                    p.brand = brand
                    p.vehicle_fitment = vehicle_fitment
                    p.description = description
                    p.price = price
                    to_update.append(p)
                    updated += 1
                else:
                    unchanged += 1

        if to_create:
            Product.objects.bulk_create(to_create)
        if to_update:
            Product.objects.bulk_update(
                to_update,
                fields=["name", "category", "brand", "vehicle_fitment", "description", "price", "updated_at"]
            )

    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
        "pages_fetched": pages_fetched,
        "requests_made": requests_made,
    }

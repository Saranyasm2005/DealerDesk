import csv
from decimal import Decimal
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Product, Dealer


class Command(BaseCommand):
    help = "Load catalogue.csv and dealers.csv into the database idempotently."

    def handle(self, *args, **options):
        data_dir = settings.DATA_DIR

        catalogue_path = data_dir / "catalogue.csv"
        dealers_path = data_dir / "dealers.csv"

        if not catalogue_path.exists():
            self.stderr.write(self.style.ERROR(f"Catalogue file not found at {catalogue_path}"))
            return
        if not dealers_path.exists():
            self.stderr.write(self.style.ERROR(f"Dealers file not found at {dealers_path}"))
            return

        with transaction.atomic():
            # Seed catalogue
            cat_count = 0
            with open(catalogue_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sku = row["sku"].strip()
                    Product.objects.update_or_create(
                        sku=sku,
                        defaults={
                            "name": row["name"].strip(),
                            "category": row["category"].strip(),
                            "brand": row["brand"].strip(),
                            "vehicle_fitment": row["vehicle_fitment"].strip(),
                            "price": Decimal(row["price"].strip()),
                            "stock": int(row["stock"].strip()),
                            "description": row["description"].strip(),
                        },
                    )
                    cat_count += 1

            # Seed dealers
            dlr_count = 0
            with open(dealers_path, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    dealer_code = row["dealer_code"].strip()
                    credit_limit_raw = row["credit_limit"].strip() if "credit_limit" in row else "0.00"
                    Dealer.objects.update_or_create(
                        dealer_code=dealer_code,
                        defaults={
                            "name": row["name"].strip(),
                            "city": row.get("city", "").strip(),
                            "phone": row.get("phone", "").strip(),
                            "email": row.get("email", "").strip(),
                            "gstin": row.get("gstin", "").strip(),
                            "credit_limit": Decimal(credit_limit_raw) if credit_limit_raw else Decimal("0.00"),
                        },
                    )
                    dlr_count += 1

        self.stdout.write(self.style.SUCCESS(f"Successfully seeded {cat_count} products and {dlr_count} dealers."))

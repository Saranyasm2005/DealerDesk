from decimal import Decimal, ROUND_HALF_UP
from rest_framework.response import Response


TAX_RATE = Decimal("0.18")
TWO_PLACES = Decimal("0.01")


def format_money(val: Decimal | str | int | float) -> str:
    """Format any amount to exactly 2 decimal places string."""
    if not isinstance(val, Decimal):
        val = Decimal(str(val))
    return f"{val.quantize(TWO_PLACES, rounding=ROUND_HALF_UP):.2f}"


def calculate_line_amounts(quantity: int, unit_price: Decimal) -> tuple[Decimal, Decimal, Decimal]:
    """
    Calculate line_subtotal, line_tax, and line_total.
    Tax is 18% calculated per line and rounded using ROUND_HALF_UP to the paisa.
    """
    line_subtotal = (unit_price * Decimal(quantity)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    line_tax = (line_subtotal * TAX_RATE).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    line_total = (line_subtotal + line_tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    return line_subtotal, line_tax, line_total


def error_response(code: str, message: str, details: list = None, status: int = 400) -> Response:
    """Standardized API error response shape required by the contract."""
    return Response(
        {
            "error": {
                "code": code,
                "message": message,
                "details": details if details is not None else [],
            }
        },
        status=status,
    )

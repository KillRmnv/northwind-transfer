from decimal import ROUND_HALF_UP, Decimal


def quantum(minor_units: int) -> Decimal:
    if minor_units < 0:
        raise ValueError("minor_units must be non-negative")
    if minor_units == 0:
        return Decimal("1")
    return Decimal("1." + "0" * minor_units)


def round_money(value: Decimal, minor_units: int) -> Decimal:
    return value.quantize(quantum(minor_units), rounding=ROUND_HALF_UP)


def format_money(value: Decimal, minor_units: int) -> str:
    return str(round_money(value, minor_units))


def amount_string_is_valid(text: str, minor_units: int) -> bool:
    if not isinstance(text, str):
        return False
    whole, dot, fraction = text.partition(".")
    if not whole.isdigit():
        return False
    if minor_units == 0:
        return dot == ""
    return dot == "." and len(fraction) == minor_units and fraction.isdigit()


def parse_money(text: str) -> Decimal:
    return Decimal(text)

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

STORAGE_CEILING = Decimal("999999999999.99999999")


def ceil_to_minor(value: Decimal, minor_units: int) -> Decimal:
    """Округление вверх до minor units: недоплата исключена."""
    return value.quantize(quantum(minor_units), rounding=ROUND_CEILING)


def quantize_rate(rate: Decimal, places: int = 8) -> Decimal:
    """Курс с фиксированной точностью, HALF_UP."""
    quantum = Decimal("1." + "0" * places)
    return rate.quantize(quantum, rounding=ROUND_HALF_UP)


def format_rate(rate: Decimal, places: int = 8) -> str:
    """Строка курса: квантованный, всегда 8 знаков."""
    return f"{quantize_rate(rate, places):f}"


def quantum(minor_units: int) -> Decimal:
    """Шаг квантования для числа знаков."""
    if minor_units < 0:
        raise ValueError("minor_units must be non-negative")
    if minor_units == 0:
        return Decimal("1")
    return Decimal("1." + "0" * minor_units)


def round_money(value: Decimal, minor_units: int) -> Decimal:
    """Денежное округление HALF_UP по minor units."""
    return value.quantize(quantum(minor_units), rounding=ROUND_HALF_UP)


def format_money(value: Decimal, minor_units: int) -> str:
    """Строка суммы для API: округлено по minor units."""
    return str(round_money(value, minor_units))


def amount_string_is_valid(text: str, minor_units: int) -> bool:
    """Строгая проверка денежной строки под minor units."""
    if not isinstance(text, str):
        return False
    whole, dot, fraction = text.partition(".")
    if not whole.isdigit():
        return False
    if minor_units == 0:
        return dot == ""
    return dot == "." and len(fraction) == minor_units and fraction.isdigit()


def parse_money(text: str) -> Decimal:
    """Разбор денежной строки в Decimal."""
    return Decimal(text)


def ensure_storable(*amounts: Decimal, field: str = "amount") -> None:
    """Защита от переполнения NUMERIC(20, 8)."""
    from app.core.exceptions import ValidationError

    for amount in amounts:
        if abs(amount) > STORAGE_CEILING:
            raise ValidationError(f"{field} exceeds storage precision")

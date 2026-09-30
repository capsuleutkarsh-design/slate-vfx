"""
Money, written and added up the same way everywhere.

Bidding hard-coded '$' in a dozen places, added floats, and let each widget
group digits by the machine's locale - so the new-bid dialog showed
'$ 20,70,00,000.00' (Indian grouping from Windows) while the table beside it
showed '$207,000,000.00', and the pipeline card rounded to whole dollars.

The studio works in Indian rupees by default and bids foreign clients in
their own currency, so:

    format_money(20700000, "INR")                -> '₹2,07,00,000.00'
    format_money(20700000, "INR", compact=True)  -> '₹2.07 Cr'
    format_money(3850000, "INR", compact=True)   -> '₹38.5 L'
    format_money(207000000, "USD")               -> '$207,000,000.00'
    format_money(380328958, "USD", compact=True) -> '$380.3M'

Rupees are grouped the Indian way (2,07,00,000), everything else in
thousands. Amounts are Decimal, never float: 0.1 + 0.2 is 0.3 here, and a
bid that is the sum of a hundred line items adds up to the paisa.

The studio's currency, day rates and GST rate are studio settings (see
slate.core.infra.studio_settings) so every workstation agrees; studio_currency(),
day_rate() and default_tax_rate() read them. A bid carries its own currency.
Nothing here converts between currencies - there is no exchange rate that
would be right, so totals in different currencies are kept apart
(see sum_by_currency).
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Iterable, Optional, Tuple, Union

Number = Union[int, float, str, Decimal, None]


@dataclass(frozen=True)
class Currency:
    code: str
    symbol: str
    name: str
    indian_grouping: bool = False


CURRENCIES: "OrderedDict[str, Currency]" = OrderedDict((
    ("INR", Currency("INR", "₹", "Indian rupee", indian_grouping=True)),
    ("USD", Currency("USD", "$", "US dollar")),
    ("EUR", Currency("EUR", "€", "Euro")),
    ("GBP", Currency("GBP", "£", "Pound sterling")),
))

DEFAULT_CURRENCY = "INR"

# Rupees are counted in lakhs and crores in the studio's own conversation, so
# the compact form uses them rather than millions.
LAKH = Decimal(100000)
CRORE = Decimal(10000000)

CENT = Decimal("0.01")


def currency(code: Optional[str]) -> Currency:
    """The Currency for a code; the studio default for anything unknown."""
    key = str(code or "").strip().upper()
    if key in CURRENCIES:
        return CURRENCIES[key]
    return CURRENCIES[DEFAULT_CURRENCY]


def normalise_code(code: Optional[str], default: Optional[str] = None) -> str:
    """'inr' -> 'INR'; anything unknown -> default (or the studio currency)."""
    key = str(code or "").strip().upper()
    if key in CURRENCIES:
        return key
    return default if default is not None else studio_currency()


def to_decimal(value: Number) -> Decimal:
    """
    An exact amount from whatever arrived: Decimal, int, float (through its
    shortest repr, so 0.1 stays 0.1), or text with symbols and separators
    ('₹2,07,00,000.50', '$ 1,234', '(500)' for a negative). None or '' is 0.
    """
    if value is None:
        return Decimal(0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(repr(value))
    text = str(value).strip()
    if not text:
        return Decimal(0)
    negative = text.startswith("(") and text.endswith(")")
    for mark in [c.symbol for c in CURRENCIES.values()] + list(CURRENCIES) + [",", " ", "(", ")", " "]:
        text = text.replace(mark, "")
    if text.startswith("-"):
        negative, text = True, text[1:]
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Not an amount: {value!r}")
    return -amount if negative else amount


def parse_money(value: Number) -> Decimal:
    """Text a person typed -> Decimal (to the paisa / cent). Raises ValueError."""
    return quantize(to_decimal(value))


def quantize(amount: Number) -> Decimal:
    """Rounded to two places, half away from zero - how an invoice rounds."""
    return to_decimal(amount).quantize(CENT, rounding=ROUND_HALF_UP)


def _group(digits: str, indian: bool) -> str:
    """'20700000' -> '2,07,00,000' (Indian) or '20,700,000'."""
    if len(digits) <= 3:
        return digits
    if not indian:
        parts = []
        while digits:
            parts.insert(0, digits[-3:])
            digits = digits[:-3]
        return ",".join(parts)
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while head:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return ",".join(parts + [tail])


def _trim(number: Decimal, places: int) -> str:
    """A short figure for the compact form: '2.07', '38.5', '12'."""
    text = f"{number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def format_money(amount: Number, code: Optional[str] = None, *, compact: bool = False,
                 symbol: bool = True, decimals: int = 2) -> str:
    """
    The amount as people read it, in the given currency (default: the studio's).

    compact=True gives the short form for cards and summaries - '₹2.07 Cr',
    '₹38.5 L', '$380.3M' - which should always carry the exact figure in a
    tooltip. symbol=False leaves the symbol off (for a column whose header
    already says the currency).
    """
    cur = currency(code or studio_currency())
    value = to_decimal(amount)
    negative = value < 0
    value = abs(value)
    sign = "-" if negative else ""
    prefix = cur.symbol if symbol else ""

    if compact:
        if cur.indian_grouping:
            if value >= CRORE:
                return f"{sign}{prefix}{_trim(value / CRORE, 2)} Cr"
            if value >= LAKH:
                return f"{sign}{prefix}{_trim(value / LAKH, 1)} L"
        else:
            for size, suffix, places in ((Decimal(10) ** 9, "B", 1),
                                         (Decimal(10) ** 6, "M", 1),
                                         (Decimal(10) ** 3, "K", 1)):
                if value >= size:
                    return f"{sign}{prefix}{_trim(value / size, places)}{suffix}"
        decimals = 0

    rounded = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    text = f"{rounded:f}"
    whole, _, fraction = text.partition(".")
    grouped = _group(whole, cur.indian_grouping)
    body = f"{grouped}.{fraction}" if decimals > 0 else grouped
    return f"{sign}{prefix}{body}"


def sum_money(amounts: Iterable[Number]) -> Decimal:
    """An exact total."""
    total = Decimal(0)
    for amount in amounts:
        total += to_decimal(amount)
    return total


def sum_by_currency(pairs: Iterable[Tuple[Number, Optional[str]]]) -> "OrderedDict[str, Decimal]":
    """
    Totals kept apart per currency - (amount, code) pairs in, {code: total} out,
    in CURRENCIES order. Adding rupees to dollars would give a number that is
    not money in any currency.
    """
    totals: Dict[str, Decimal] = {}
    for amount, code in pairs:
        key = normalise_code(code, DEFAULT_CURRENCY)
        totals[key] = totals.get(key, Decimal(0)) + to_decimal(amount)
    return OrderedDict((k, totals[k]) for k in CURRENCIES if k in totals)


def format_totals(totals: Dict[str, Number], *, compact: bool = False, empty: str = "") -> str:
    """'₹3.80 Cr + $380.3M' for totals from sum_by_currency()."""
    parts = [format_money(v, k, compact=compact) for k, v in totals.items()]
    if not parts:
        return empty or format_money(0, studio_currency(), compact=compact)
    return " + ".join(parts)


def percent_of(amount: Number, rate_percent: Number) -> Decimal:
    """rate_percent % of amount, to the paisa - GST on a subtotal, a margin."""
    return quantize(to_decimal(amount) * to_decimal(rate_percent) / Decimal(100))


# ------------------------------------------------------------ studio settings

def studio_currency() -> str:
    """The studio's own currency (a studio setting; INR unless changed)."""
    try:
        from slate.core.infra.studio_settings import get_setting
        return normalise_code(get_setting("currency"), DEFAULT_CURRENCY)
    except Exception:
        return DEFAULT_CURRENCY


def day_rate(code: Optional[str] = None) -> Optional[Decimal]:
    """
    The studio's artist day rate in this currency, or None when the studio
    has not set one for it (there is no exchange rate to derive it from).
    """
    key = normalise_code(code)
    try:
        from slate.core.infra.studio_settings import get_setting
        rates = get_setting("day_rates") or {}
    except Exception:
        rates = {}
    if key in rates and rates[key] not in (None, ""):
        try:
            return to_decimal(rates[key])
        except ValueError:
            return None
    return None


def default_tax_rate(code: Optional[str] = None) -> Decimal:
    """
    The tax a new bid in this currency starts with, in percent: the studio's
    GST rate (18 unless changed) for rupee bids, 0 for foreign clients.
    Editable per bid; this is only where it starts.
    """
    if normalise_code(code) != "INR":
        return Decimal(0)
    try:
        from slate.core.infra.studio_settings import get_setting
        return to_decimal(get_setting("gst_rate"))
    except Exception:
        return Decimal(18)

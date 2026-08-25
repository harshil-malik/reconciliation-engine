from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd


def to_decimal(value: Any) -> Decimal:
    """Parse a raw amount cell (string, float, NaN, blank) into a Decimal.

    Handles the common statement/ledger formatting quirks: thousands separators,
    blank cells for "no withdrawal"/"no deposit", and stray dashes.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return Decimal("0")
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if value in ("", "-"):
            return Decimal("0")
    try:
        if pd.isna(value):
            return Decimal("0")
    except (TypeError, ValueError):
        pass
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Could not parse amount: {value!r}") from exc

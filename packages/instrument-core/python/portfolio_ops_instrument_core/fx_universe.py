from __future__ import annotations


SUPPORTED_FX_CURRENCIES: tuple[str, ...] = ("USD", "HKD", "CNY")
PIVOT_CURRENCY = "USD"
MAINTAINED_FX_INSTRUMENTS: dict[tuple[str, str], str] = {
    ("USD", "HKD"): "fx-usd-hkd",
    ("USD", "CNY"): "fx-usd-cny",
}

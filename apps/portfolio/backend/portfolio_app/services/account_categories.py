from __future__ import annotations

from collections.abc import Mapping


ACCOUNT_CATEGORIES = ("cash", "security", "fcn", "option")
HOLDING_ACCOUNT_CATEGORIES = ("security", "fcn", "option")
SECURITY_INSTRUMENT_TYPES = ("equity", "etf", "fund", "other")

_INSTRUMENT_CATEGORY_BY_TYPE = {
    **{instrument_type: "security" for instrument_type in SECURITY_INSTRUMENT_TYPES},
    "fcn": "fcn",
    "option": "option",
}


def normalize_account_category(account_category: object) -> str:
    normalized = str(account_category or "").strip().lower()
    if normalized not in ACCOUNT_CATEGORIES:
        raise ValueError("Account category must be Cash, Security, FCN, or Option.")
    return normalized


def validate_account_category(*, account_type: object, account_category: object) -> str:
    normalized_type = str(account_type or "").strip().lower()
    normalized_category = normalize_account_category(account_category)
    expected_type = account_type_for_category(normalized_category)
    if normalized_type != expected_type:
        raise ValueError(
            f"{normalized_category.title()} category requires account type {expected_type}."
        )
    return normalized_category


def account_category_from_record(account: Mapping[str, object]) -> str:
    return validate_account_category(
        account_type=account.get("account_type"),
        account_category=account.get("account_category"),
    )


def account_type_for_category(account_category: object) -> str:
    normalized = normalize_account_category(account_category)
    if normalized == "cash":
        return "deposit_account"
    return "securities_account"


def asset_account_category(
    *,
    instrument_ref: Mapping[str, object] | None,
    derivative_contract: Mapping[str, object] | None,
) -> str | None:
    if derivative_contract is not None:
        contract_type = str(derivative_contract.get("contract_type") or "").strip().lower()
        return _INSTRUMENT_CATEGORY_BY_TYPE.get(contract_type)
    if instrument_ref is not None:
        instrument_type = str(instrument_ref.get("instrument_type") or "").strip().lower()
        return _INSTRUMENT_CATEGORY_BY_TYPE.get(instrument_type)
    return None

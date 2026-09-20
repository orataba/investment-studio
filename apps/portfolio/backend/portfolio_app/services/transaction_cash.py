"""Transaction cash-effect semantics shared by projections and API assembly."""
from portfolio_app.services.option_actions import resolve_option_action


def resolve_transaction_net_cash_effect(
    record: dict[str, object],
) -> float | None:
    transaction_type = str(record.get("transaction_type") or "")
    option_action = resolve_option_action(record)
    gross_amount = float(record.get("gross_amount") or 0.0)
    fees = float(record.get("fees") or 0.0)
    taxes = float(record.get("taxes") or 0.0)
    if option_action in {"buy_to_open", "buy_to_close"}:
        return -(gross_amount + fees + taxes)
    if option_action in {"sell_to_close", "sell_to_open"}:
        return gross_amount - fees - taxes
    if transaction_type in {"buy", "buy_to_cover"}:
        return -(gross_amount + fees + taxes)
    if transaction_type in {"sell", "short_sell"}:
        return gross_amount - fees - taxes
    if transaction_type == "maturity_redemption":
        return gross_amount - fees - taxes
    if transaction_type in {"dividend", "coupon", "return_of_capital"}:
        return gross_amount - fees - taxes
    if transaction_type == "interest":
        return gross_amount
    if transaction_type == "dividend_reinvestment":
        return 0.0
    if transaction_type in {"option_opening_balance", "short_opening_balance"}:
        return 0.0
    if transaction_type == "lifecycle_event":
        if record.get("lifecycle_event_type") == "option_writer_cash_settlement":
            return -(gross_amount + fees + taxes)
        return 0.0
    if transaction_type in {"fee", "tax"}:
        return -gross_amount
    if transaction_type == "deposit":
        return gross_amount
    if transaction_type == "withdrawal":
        return -gross_amount
    if transaction_type == "fx_conversion":
        return None
    if transaction_type == "transfer_out" and record.get("transfer_object_type") == "cash":
        return -gross_amount
    if transaction_type == "transfer_in" and record.get("transfer_object_type") == "cash":
        return gross_amount
    return None

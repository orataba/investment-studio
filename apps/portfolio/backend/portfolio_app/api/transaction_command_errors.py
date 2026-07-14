"""Shared HTTP mapping for prospective transaction-book validation failures."""

from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from portfolio_app.services.transaction_command_validator import (
    TransactionCommandValidationCode,
    TransactionCommandValidationError,
)


def raise_transaction_command_validation_error(
    error: TransactionCommandValidationError,
) -> NoReturn:
    """Raise the stable public error contract for an exact replay rejection."""

    status_code = (
        422
        if error.code is TransactionCommandValidationCode.EVENT_BUILD_FAILED
        else 409
    )
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": "transaction_command_validation_failed",
            "validation_code": error.code.value,
            "reason_code": error.reason_code,
            "failed_event_id": error.failed_event_id,
            "message": str(error),
        },
    ) from error


__all__ = ["raise_transaction_command_validation_error"]

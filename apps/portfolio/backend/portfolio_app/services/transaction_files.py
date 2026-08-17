from __future__ import annotations

from pathlib import Path

from portfolio_app.services.transaction_csv import MAX_CSV_BYTES
from portfolio_app.services.transaction_xlsx import transaction_xlsx_to_csv


SUPPORTED_TRANSACTION_FILE_EXTENSIONS = frozenset({".csv", ".xlsx"})


def transaction_upload_to_csv(filename: str | None, content: bytes) -> str:
    extension = Path(str(filename or "")).suffix.lower()
    if extension not in SUPPORTED_TRANSACTION_FILE_EXTENSIONS:
        raise ValueError("Transaction import files must use .csv or .xlsx.")
    if extension == ".xlsx":
        return transaction_xlsx_to_csv(content)
    if not content:
        raise ValueError("CSV content is required.")
    if len(content) > MAX_CSV_BYTES:
        raise ValueError("CSV content exceeds the 5 MB limit.")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV files must use UTF-8 encoding.") from error

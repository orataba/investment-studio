from __future__ import annotations

import csv
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO, StringIO
from typing import Iterable
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.cell import Cell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from portfolio_app.services.transaction_csv import (
    IMPORT_COLUMNS,
    MAX_CSV_BYTES,
    MAX_CSV_ROWS,
    transaction_export_rows,
)


TRANSACTION_SHEET_NAME = "Transactions"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

DATE_COLUMNS = frozenset(
    {
        "trade_date",
        "settlement_date",
        "position_effective_date",
        "entitlement_date",
        "acquisition_date",
        "option_expiry_date",
        "fcn_issue_date",
        "fcn_final_observation_date",
        "fcn_maturity_date",
    }
)
NUMERIC_COLUMNS = frozenset(
    {
        "option_strike",
        "option_contract_multiplier",
        "fcn_notional",
        "fcn_annual_coupon_rate_pct",
        "quantity",
        "price",
        "gross_amount",
        "counter_amount",
        "fx_rate",
        "fees",
        "taxes",
    }
)

HEADER_FILL = PatternFill(fill_type="solid", fgColor="1F2937")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _export_value(column: str, value: object) -> object:
    if value is None or value == "":
        return None
    if column in DATE_COLUMNS and isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    if column == "trade_time" and isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return value
    if column in NUMERIC_COLUMNS:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return str(value)
    return value if isinstance(value, (date, datetime, time, Decimal, int, float)) else str(value)


def _set_text_cell(cell: Cell, value: str) -> None:
    cell.value = value
    cell.data_type = "s"


def render_transaction_xlsx(records: Iterable[dict[str, object]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = TRANSACTION_SHEET_NAME
    worksheet.freeze_panes = "A2"
    worksheet.sheet_view.showGridLines = False

    for column_index, column in enumerate(IMPORT_COLUMNS, start=1):
        cell = worksheet.cell(row=1, column=column_index)
        _set_text_cell(cell, column)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    worksheet.row_dimensions[1].height = 24

    rows = transaction_export_rows(records)
    for row_index, row in enumerate(rows, start=2):
        for column_index, column in enumerate(IMPORT_COLUMNS, start=1):
            value = _export_value(column, row.get(column))
            if value is None:
                continue
            cell = worksheet.cell(row=row_index, column=column_index)
            if isinstance(value, str):
                _set_text_cell(cell, value)
            else:
                cell.value = value
            if column in DATE_COLUMNS:
                cell.number_format = "yyyy-mm-dd"
            elif column == "trade_time":
                cell.number_format = "hh:mm"
            elif column in NUMERIC_COLUMNS:
                cell.number_format = "0.########"

    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(IMPORT_COLUMNS))}{max(1, len(rows) + 1)}"
    for index, column in enumerate(IMPORT_COLUMNS, start=1):
        width = max(12, min(34, len(column) + 2))
        worksheet.column_dimensions[get_column_letter(index)].width = width

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def render_transaction_xlsx_template() -> bytes:
    return render_transaction_xlsx(())


def _cell_text(cell: Cell, column: str) -> str:
    if cell.data_type == "f":
        raise ValueError(
            f"{TRANSACTION_SHEET_NAME}!{cell.coordinate} contains a formula; "
            "transaction imports require literal values."
        )
    if cell.data_type == "e":
        raise ValueError(
            f"{TRANSACTION_SHEET_NAME}!{cell.coordinate} contains an Excel error value."
        )
    value = cell.value
    if value is None:
        return ""
    if isinstance(value, datetime):
        if column in DATE_COLUMNS:
            return value.date().isoformat()
        if column == "trade_time":
            return value.time().replace(microsecond=0).isoformat(timespec="minutes")
        return value.replace(microsecond=0).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.replace(microsecond=0).isoformat(timespec="minutes")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError(
                f"{TRANSACTION_SHEET_NAME}!{cell.coordinate} contains a non-finite number."
            )
        return format(Decimal(str(value)), "f")
    return str(value)


def transaction_xlsx_to_csv(xlsx_content: bytes) -> str:
    if not xlsx_content:
        raise ValueError("Excel content is required.")
    if len(xlsx_content) > MAX_CSV_BYTES:
        raise ValueError("Excel content exceeds the 5 MB limit.")
    try:
        workbook = load_workbook(
            BytesIO(xlsx_content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except (BadZipFile, InvalidFileException, OSError, ValueError) as error:
        raise ValueError("The Excel file is not a valid .xlsx workbook.") from error
    try:
        if TRANSACTION_SHEET_NAME not in workbook.sheetnames:
            raise ValueError(
                f"Excel workbook must contain a '{TRANSACTION_SHEET_NAME}' worksheet."
            )
        worksheet = workbook[TRANSACTION_SHEET_NAME]
        output = StringIO(newline="")
        writer = csv.writer(output, lineterminator="\r\n")
        header_names: list[str] = []
        for row_index, cells in enumerate(worksheet.iter_rows(), start=1):
            if row_index > MAX_CSV_ROWS + 1:
                raise ValueError("Excel content exceeds the 5,000 row limit.")
            if row_index == 1:
                header_names = [str(cell.value or "").strip() for cell in cells]
            writer.writerow(
                [
                    _cell_text(
                        cell,
                        header_names[column_index] if row_index > 1 and column_index < len(header_names) else "",
                    )
                    for column_index, cell in enumerate(cells)
                ]
            )
        return "\ufeff" + output.getvalue()
    finally:
        workbook.close()

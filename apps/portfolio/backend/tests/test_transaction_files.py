from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import load_workbook

from portfolio_app.services.transaction_csv import (
    IMPORT_COLUMNS,
    parse_transaction_csv,
    render_transaction_csv,
)
from portfolio_app.services.transaction_xlsx import (
    TRANSACTION_SHEET_NAME,
    XLSX_MEDIA_TYPE,
    render_transaction_xlsx,
    render_transaction_xlsx_template,
    transaction_xlsx_to_csv,
)


def _deposit_record(external_reference: str) -> dict[str, object]:
    return {
        "transaction_sequence": 9001,
        "transaction_type": "deposit",
        "trade_date": "2026-05-25",
        "account_id": "cash-usd-main",
        "gross_amount": 25.12345678,
        "source_gross_amount": "25.12345678",
        "fees": 0,
        "taxes": 0,
        "fee_category": "unknown",
        "currency": "USD",
        "source_system": "transaction_file_test",
        "external_reference": external_reference,
        "note": "=literal note",
    }


def test_transaction_xlsx_is_typed_safe_and_round_trip_importable() -> None:
    content = render_transaction_xlsx([_deposit_record("xlsx-round-trip-1")])
    workbook = load_workbook(BytesIO(content), data_only=False)
    worksheet = workbook[TRANSACTION_SHEET_NAME]

    assert worksheet.freeze_panes == "A2"
    assert worksheet.auto_filter.ref == f"A1:AR2"
    assert worksheet.cell(1, 1).value == "transaction_type"
    note_cell = worksheet.cell(2, IMPORT_COLUMNS.index("note") + 1)
    assert note_cell.value == "=literal note"
    assert note_cell.data_type == "s"
    workbook.close()

    _headers, rows = parse_transaction_csv(transaction_xlsx_to_csv(content))
    assert rows[0].errors == ()
    assert rows[0].transaction is not None
    assert str(rows[0].transaction.gross_amount) == "25.12345678"
    assert rows[0].transaction.note == "=literal note"


def test_transaction_xlsx_rejects_formula_cells() -> None:
    workbook = load_workbook(BytesIO(render_transaction_xlsx_template()))
    worksheet = workbook[TRANSACTION_SHEET_NAME]
    values = {
        "transaction_type": "deposit",
        "trade_date": "2026-05-25",
        "account_id": "cash-usd-main",
        "gross_amount": 25,
        "currency": "USD",
        "note": "=1+1",
    }
    for column, value in values.items():
        worksheet.cell(2, IMPORT_COLUMNS.index(column) + 1).value = value
    output = BytesIO()
    workbook.save(output)
    workbook.close()

    with pytest.raises(ValueError, match="contains a formula"):
        transaction_xlsx_to_csv(output.getvalue())


def test_transaction_file_api_supports_csv_and_xlsx_with_one_validation_path(client) -> None:
    record = _deposit_record("xlsx-api-import-1")
    csv_content = render_transaction_csv([record]).encode("utf-8")
    xlsx_content = render_transaction_xlsx([record])

    csv_preview_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/files/preview",
        files={"file": ("transactions.csv", csv_content, "text/csv")},
        data={"default_source_system": "portfolio_file_upload"},
    )
    assert csv_preview_response.status_code == 200, csv_preview_response.text
    assert csv_preview_response.json()["error_count"] == 0

    xlsx_preview_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/files/preview",
        files={"file": ("transactions.xlsx", xlsx_content, XLSX_MEDIA_TYPE)},
        data={"default_source_system": "portfolio_file_upload"},
    )
    assert xlsx_preview_response.status_code == 200, xlsx_preview_response.text
    preview = xlsx_preview_response.json()
    assert preview["valid_count"] == 1
    assert preview["error_count"] == 0

    import_response = client.post(
        "/api/portfolios/portfolio-ops/transactions/files/import",
        headers={"Idempotency-Key": "xlsx-api-import-1"},
        files={"file": ("transactions.xlsx", xlsx_content, XLSX_MEDIA_TYPE)},
        data={
            "default_source_system": "portfolio_file_upload",
            "preview_digest": preview["preview_digest"],
        },
    )
    assert import_response.status_code == 200, import_response.text
    assert import_response.json()["created_count"] == 1

    export_response = client.get("/api/portfolios/portfolio-ops/transactions.xlsx")
    assert export_response.status_code == 200
    assert export_response.headers["content-type"].startswith(XLSX_MEDIA_TYPE)
    _headers, exported_rows = parse_transaction_csv(
        transaction_xlsx_to_csv(export_response.content)
    )
    assert any(
        row.transaction is not None
        and row.transaction.external_reference == "xlsx-api-import-1"
        for row in exported_rows
    )

    template_response = client.get(
        "/api/portfolios/portfolio-ops/transactions/xlsx-template"
    )
    assert template_response.status_code == 200
    template_workbook = load_workbook(BytesIO(template_response.content))
    assert template_workbook[TRANSACTION_SHEET_NAME].max_row == 1
    template_workbook.close()


def test_transaction_file_api_rejects_unsupported_extensions(client) -> None:
    response = client.post(
        "/api/portfolios/portfolio-ops/transactions/files/preview",
        files={"file": ("transactions.xls", b"legacy", "application/vnd.ms-excel")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Transaction import files must use .csv or .xlsx."

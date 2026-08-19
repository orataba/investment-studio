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
    EXAMPLES_SHEET_NAME,
    FIELD_GUIDE_SHEET_NAME,
    INSTRUCTIONS_SHEET_NAME,
    LIFECYCLE_EVENT_VALUES,
    LISTS_SHEET_NAME,
    TEMPLATE_INPUT_LAST_ROW,
    TRANSACTION_SHEET_NAME,
    TRANSACTION_TYPE_VALUES,
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
    assert worksheet.auto_filter.ref == "A1:AR2"
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


def test_transaction_xlsx_template_guides_manual_entry_without_importing_examples() -> (
    None
):
    content = render_transaction_xlsx_template()
    workbook = load_workbook(BytesIO(content), data_only=False)

    assert workbook.sheetnames == [
        INSTRUCTIONS_SHEET_NAME,
        TRANSACTION_SHEET_NAME,
        FIELD_GUIDE_SHEET_NAME,
        EXAMPLES_SHEET_NAME,
        LISTS_SHEET_NAME,
    ]
    assert workbook.active.title == INSTRUCTIONS_SHEET_NAME

    worksheet = workbook[TRANSACTION_SHEET_NAME]
    assert worksheet.max_row == 1
    assert worksheet.max_column == len(IMPORT_COLUMNS)
    assert worksheet.freeze_panes == "A2"
    assert worksheet.auto_filter.ref == f"A1:AR{TEMPLATE_INPUT_LAST_ROW}"
    assert worksheet.cell(1, 1).comment is not None
    assert "交易类型" in str(worksheet.cell(1, 1).comment.text)

    named_ranges = set(workbook.defined_names)
    assert {
        "TransactionTypeValues",
        "LifecycleEventValues",
        "FeeCategoryValues",
        "CurrencyValues",
        "TransferObjectTypeValues",
        "DerivativeContractTypeValues",
        "OptionTypeValues",
    } <= named_ranges
    validations = worksheet.data_validations.dataValidation
    transaction_type_validation = next(
        item
        for item in validations
        if str(item.sqref) == f"A2:A{TEMPLATE_INPUT_LAST_ROW}"
    )
    currency_validation = next(
        item
        for item in validations
        if str(item.sqref) == f"AN2:AN{TEMPLATE_INPUT_LAST_ROW}"
    )
    assert transaction_type_validation.type == "list"
    assert transaction_type_validation.formula1 == "TransactionTypeValues"
    assert currency_validation.type == "list"
    assert currency_validation.formula1 == "CurrencyValues"

    examples = workbook[EXAMPLES_SHEET_NAME]
    assert examples.cell(1, 1).value == "交易类型示例（仅供参考，不会上传）"
    assert examples.cell(2, 1).value is not None
    assert [
        examples.cell(4, index).value for index in range(2, len(IMPORT_COLUMNS) + 2)
    ] == list(IMPORT_COLUMNS)
    example_scenarios = {
        str(examples.cell(row_index, 1).value)
        for row_index in range(5, examples.max_row + 1)
    }
    for row_index in range(5, examples.max_row + 1):
        for column_index in range(1, len(IMPORT_COLUMNS) + 1):
            worksheet.cell(row_index, column_index).value = examples.cell(
                row_index,
                column_index + 1,
            ).value
    example_workbook = BytesIO()
    workbook.save(example_workbook)
    workbook.close()

    _headers, rows = parse_transaction_csv(
        transaction_xlsx_to_csv(example_workbook.getvalue())
    )
    assert len(rows) >= 40
    assert all(row.errors == () for row in rows)
    assert {
        str(row.transaction.currency) for row in rows if row.transaction is not None
    } == {"USD", "HKD", "CNY"}
    assert {
        "internal_transfer"
        if row.internal_transfer is not None
        else str(row.transaction.transaction_type)
        for row in rows
    } == set(TRANSACTION_TYPE_VALUES)
    assert {
        str(row.transaction.lifecycle_event_type)
        for row in rows
        if row.transaction is not None
        and row.transaction.lifecycle_event_type is not None
    } == set(LIFECYCLE_EVENT_VALUES)
    assert {
        "股票｜USD 股票买入",
        "ETF｜HKD ETF 卖出",
        "公募基金｜CNY 申购确认",
        "私募基金｜CNY 赎回确认",
        "证券｜HKD 独立交易费用",
        "证券｜HKD 独立税费",
        "Option｜CNY 合约期初多头",
        "Option｜HKD 独立费用",
        "Option｜HKD 独立税费",
        "Option 实物行权拆分 1/2｜多头现金结算",
        "Option 实物行权拆分 2/2｜标的股票买入",
        "Option 空头 Call 指派拆分 1/2｜空头现金结算",
        "Option 空头 Call 指派拆分 2/2｜标的股票卖出",
        "FCN｜新合约进入",
        "FCN｜HKD 合约期初持仓",
        "FCN｜提前退出",
        "FCN｜票息收入",
        "FCN｜独立费用",
        "FCN｜独立税费",
        "FCN 敲入交付拆分 1/2｜敲入结束",
        "FCN 敲入交付拆分 2/2｜交付证券买入",
    } <= example_scenarios


def test_transaction_file_api_supports_csv_and_xlsx_with_one_validation_path(
    client,
) -> None:
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
    assert (
        response.json()["detail"] == "Transaction import files must use .csv or .xlsx."
    )

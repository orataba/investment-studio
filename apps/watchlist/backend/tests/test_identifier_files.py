from __future__ import annotations

from io import BytesIO

import pytest
from openpyxl import Workbook

from watchlist_app.services.identifier_files import parse_identifier_file


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Identifiers"
    for row in rows:
        worksheet.append(row)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_identifier_csv_uses_real_csv_rows_and_deduplicates() -> None:
    content = (
        'Identifier,Note\r\nAGG,"first line\r\nsecond line"\r\nAGG,duplicate\r\nSXV264,ok\r\n'
    ).encode("utf-8")

    assert parse_identifier_file("identifiers.csv", content) == ["AGG", "SXV264"]


def test_identifier_xlsx_accepts_the_same_header_contract() -> None:
    content = _workbook_bytes(
        [["Ticker / ISIN", "Name"], ["AGG", "Bond ETF"], ["SXV264", "Fund"]]
    )

    assert parse_identifier_file("identifiers.xlsx", content) == ["AGG", "SXV264"]


def test_identifier_xlsx_rejects_formula_cells() -> None:
    content = _workbook_bytes([["Identifier"], ["=CONCAT(\"AG\",\"G\")"]])

    with pytest.raises(ValueError, match="contains a formula"):
        parse_identifier_file("identifiers.xlsx", content)


def test_resolve_file_endpoint_resolves_csv_and_excel(client) -> None:
    csv_response = client.post(
        "/api/instruments/resolve-file",
        files={"file": ("identifiers.csv", b"Identifier\nAGG\nSXV264\n", "text/csv")},
    )
    assert csv_response.status_code == 200, csv_response.text
    assert [item["status"] for item in csv_response.json()["results"]] == [
        "resolved",
        "resolved",
    ]

    xlsx_response = client.post(
        "/api/instruments/resolve-file",
        files={
            "file": (
                "identifiers.xlsx",
                _workbook_bytes([["ISIN"], ["AGG"], ["MISSING"]]),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert xlsx_response.status_code == 200, xlsx_response.text
    assert [item["status"] for item in xlsx_response.json()["results"]] == [
        "resolved",
        "not_found",
    ]

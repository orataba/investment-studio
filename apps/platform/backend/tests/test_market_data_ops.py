from __future__ import annotations

from io import BytesIO
import zipfile

from openpyxl import Workbook

from app.services.market_data_ops import (
    _filter_rows_for_rule,
    _parse_nav_rows_from_label_snapshot_matrix,
    _parse_nav_rows_from_xlsx,
)


def _workbook_bytes(rows: list[list[object]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _with_broken_dimension(file_bytes: bytes) -> bytes:
    source = BytesIO(file_bytes)
    output = BytesIO()
    with zipfile.ZipFile(source) as reader, zipfile.ZipFile(output, "w") as writer:
        for info in reader.infolist():
            payload = reader.read(info.filename)
            if info.filename == "xl/worksheets/sheet1.xml":
                payload = payload.replace(b'dimension ref="A1:E2"', b'dimension ref="A1"')
            writer.writestr(info, payload)
    return output.getvalue()


def test_parse_nav_rows_from_xlsx_supports_chinese_headers() -> None:
    rows = [
        ["净值日期", "产品代码", "产品名称", "单位净值", "累计单位净值"],
        ["2026-04-14", "SBCJ69", "国泰君安期货CTA因子组合2号集合资产管理计划", "1.1002", "1.1002"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-04-14"
    assert str(parsed[0]["nav"]) == "1.1002"
    assert str(parsed[0]["nav_with_dividend"]) == "1.1002"
    assert parsed[0]["asset_code"] == "SBCJ69"
    assert parsed[0]["asset_name"] == "国泰君安期货CTA因子组合2号集合资产管理计划"
    assert parsed[0]["currency"] == "CNY"
    assert parsed[0]["frequency"] == "daily"


def test_parse_nav_rows_from_xlsx_ignores_broken_dimension_metadata() -> None:
    rows = [
        ["净值日期", "产品代码", "产品名称", "单位净值", "累计单位净值"],
        ["2026-04-14", "B3935B", "孝庸市场中性一号私募证券投资基金B", "1.3699", "1.3699"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_with_broken_dimension(_workbook_bytes(rows)))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-04-14"
    assert str(parsed[0]["nav"]) == "1.3699"
    assert str(parsed[0]["nav_with_dividend"]) == "1.3699"


def test_parse_nav_rows_from_xlsx_supports_total_nav_aliases_and_datetime_values() -> None:
    rows = [
        ["产品代码", "产品名称", "净值日期", "单位净值  (元)", "累计净值  (元)"],
        ["ANZ73A(A级)", "盈怀香柏树1号私募证券投资基金A类", "2024-09-05 00:00:00", "1", "1"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2024-09-05"
    assert str(parsed[0]["nav"]) == "1"
    assert str(parsed[0]["nav_with_dividend"]) == "1"
    assert parsed[0]["asset_code"] == "ANZ73A(A级)"
    assert parsed[0]["asset_name"] == "盈怀香柏树1号私募证券投资基金A类"


def test_parse_nav_rows_from_xlsx_supports_chinese_date_strings_after_title_rows() -> None:
    rows = [
        ["每日净值表"],
        ["日期：2024年12月05日至2025年10月12日"],
        ["日期", "产品代码", "产品名称", "单位净值", "累计单位净值"],
        ["2024年12月05日", "ARE77A", "盈怀香柏树7号私募证券投资基金A", "1.0", "1.0"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2024-12-05"
    assert parsed[0]["asset_code"] == "ARE77A"
    assert parsed[0]["asset_name"] == "盈怀香柏树7号私募证券投资基金A"
    assert str(parsed[0]["nav"]) == "1.0"
    assert str(parsed[0]["nav_with_dividend"]) == "1.0"


def test_filter_rows_for_rule_supports_exact_code_match() -> None:
    rows = [
        {
            "as_of_date": "2026-04-14",
            "asset_code": "SAZB60",
            "asset_name": "九慕云谷均衡配置私募证券投资基金",
            "nav": "1.0657",
            "nav_with_dividend": "1.0657",
        },
        {
            "as_of_date": "2026-04-14",
            "asset_code": "AZB60A",
            "asset_name": "九慕云谷均衡配置私募证券投资基金A",
            "nav": "1.0657",
            "nav_with_dividend": "1.0657",
        },
    ]

    filtered = _filter_rows_for_rule(
        rule={"row_code_equals": ["SAZB60"]},
        rows=rows,
    )

    assert len(filtered) == 1
    assert filtered[0]["asset_code"] == "SAZB60"


def test_parse_nav_rows_from_label_snapshot_matrix_extracts_nav_values() -> None:
    matrix = [
        ["招商证券股份有限公司_九慕云谷1号私募证券投资基金_专用表", None],
        ["日期：2026-04-14", None],
        ["期初单位净值", "1.0980"],
        ["单位净值", "1.1135"],
        ["累计派现金额", "0"],
        ["累计单位净值", "1.1135"],
    ]

    parsed = _parse_nav_rows_from_label_snapshot_matrix(matrix)

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-04-14"
    assert str(parsed[0]["nav"]) == "1.1135"
    assert str(parsed[0]["nav_with_dividend"]) == "1.1135"
    assert parsed[0]["currency"] == "CNY"
    assert parsed[0]["frequency"] == "daily"

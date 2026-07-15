from __future__ import annotations

from email.message import EmailMessage
from email import policy
from io import BytesIO
import zipfile

from openpyxl import Workbook
import pytest

from platform_app.services import market_data_ops
from platform_app.services.market_data_ops import (
    _apply_reinvested_total_return_correction,
    _filter_rows_for_rule,
    _import_rows_from_email_rules,
    _normalized_email_rules,
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


def test_non_fund_nav_preview_and_file_import_fail_before_parsing_or_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_data_ops,
        "get_instrument",
        lambda instrument_id: {
            "instrument_id": instrument_id,
            "instrument_type": "fx",
        },
    )

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("NAV parsing or persistence must not run for a non-fund instrument")

    monkeypatch.setattr(market_data_ops, "_parse_nav_rows_from_text", fail_if_called)
    monkeypatch.setattr(market_data_ops, "_parse_nav_rows_from_uploaded_file", fail_if_called)
    monkeypatch.setattr(market_data_ops, "replace_nav_history", fail_if_called)

    with pytest.raises(ValueError, match="only supported for fund instruments"):
        market_data_ops.preview_nav_import(
            instrument_id="fx-usd-cny",
            raw_text="not-a-nav-row",
        )

    with pytest.raises(ValueError, match="only supported for fund instruments"):
        market_data_ops.import_nav_file(
            instrument_id="fx-usd-cny",
            file_name="not-a-workbook.xlsx",
            file_bytes=b"not-a-workbook",
            provider=None,
            status="complete",
            updated_by="pytest",
        )


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


def _nav_email_bytes(*, subject: str, attachment_name: str, rows: list[list[object]]) -> bytes:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = "yywbfa@cmschina.com.cn"
    message.set_content("NAV attachment")
    message.add_attachment(
        _workbook_bytes(rows),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=attachment_name,
    )
    return message.as_bytes(policy=policy.default)


class _FakeMailbox:
    def __init__(self, messages: dict[int, bytes]) -> None:
        self.messages = messages

    def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
        if command == "search":
            return "OK", [b" ".join(str(uid).encode("ascii") for uid in sorted(self.messages))]
        if command == "fetch":
            uid = int(str(args[0]))
            payload = self.messages.get(uid)
            if payload is None:
                return "NO", []
            return "OK", [(b"1 (UID %d BODY[])" % uid, payload)]
        return "NO", []


def _zb945a_existing_nav_instrument() -> dict[str, object]:
    return {
        "market_data": [
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-02",
                "value": "0.9334",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-02",
                "value": "1.4897",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "cumulative_nav",
                "as_of_date": "2026-04-02",
                "value": "1.4897",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-03",
                "value": "0.9352",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "total_return_nav",
                "as_of_date": "2026-04-03",
                "value": "1.4925727876580244",
                "status": "complete",
            },
            {
                "metric_family": "nav",
                "quote_basis": "cumulative_nav",
                "as_of_date": "2026-04-03",
                "value": "1.4915",
                "status": "complete",
            },
        ]
    }


def test_parse_nav_rows_from_xlsx_supports_chinese_headers() -> None:
    rows = [
        ["净值日期", "产品代码", "产品名称", "单位净值", "累计单位净值"],
        ["2026-04-14", "SBCJ69", "国泰君安期货CTA因子组合2号集合资产管理计划", "1.1002", "1.1002"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-04-14"
    assert str(parsed[0]["nav"]) == "1.1002"
    assert str(parsed[0]["cumulative_nav"]) == "1.1002"
    assert parsed[0].get("nav_with_dividend") is None
    assert parsed[0]["instrument_code"] == "SBCJ69"
    assert parsed[0]["instrument_name"] == "国泰君安期货CTA因子组合2号集合资产管理计划"
    assert "currency" not in parsed[0]
    assert parsed[0]["frequency"] == "daily"


def test_prepare_nav_rows_uses_selected_fund_currency_and_rejects_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_data_ops,
        "_existing_nav_history_by_date",
        lambda _instrument_id: {},
    )
    instrument = {"currency": "HKD"}
    source_row = {
        "as_of_date": "2026-04-14",
        "nav": "1.1002",
        "frequency": "daily",
    }

    prepared = market_data_ops._prepare_nav_rows_for_instrument(
        instrument=instrument,
        instrument_id="fund-hkd",
        rows=[source_row],
    )

    assert prepared[0]["currency"] == "HKD"
    with pytest.raises(ValueError, match="does not match the selected fund currency"):
        market_data_ops._prepare_nav_rows_for_instrument(
            instrument=instrument,
            instrument_id="fund-hkd",
            rows=[{**source_row, "currency": "USD"}],
        )


def test_parse_nav_rows_from_xlsx_supports_product_code_and_trade_date_aliases() -> None:
    rows = [
        ["产品编码", "产品名称", "交易日期", "单位净值", "累计净值"],
        ["SBMM07", "国泰君安期货CTA因子组合3号集合资产管理计划", "20260529", "1.0148", "1.0148"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-05-29"
    assert parsed[0]["instrument_code"] == "SBMM07"
    assert parsed[0]["instrument_name"] == "国泰君安期货CTA因子组合3号集合资产管理计划"
    assert str(parsed[0]["nav"]) == "1.0148"
    assert str(parsed[0]["cumulative_nav"]) == "1.0148"


def test_parse_nav_rows_from_xlsx_ignores_broken_dimension_metadata() -> None:
    rows = [
        ["净值日期", "产品代码", "产品名称", "单位净值", "累计单位净值"],
        ["2026-04-14", "B3935B", "孝庸市场中性一号私募证券投资基金B", "1.3699", "1.3699"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_with_broken_dimension(_workbook_bytes(rows)))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-04-14"
    assert str(parsed[0]["nav"]) == "1.3699"
    assert str(parsed[0]["cumulative_nav"]) == "1.3699"


def test_parse_nav_rows_from_xlsx_supports_total_nav_aliases_and_datetime_values() -> None:
    rows = [
        ["产品代码", "产品名称", "净值日期", "单位净值  (元)", "累计净值  (元)"],
        ["ANZ73A(A级)", "盈怀香柏树1号私募证券投资基金A类", "2024-09-05 00:00:00", "1", "1"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2024-09-05"
    assert str(parsed[0]["nav"]) == "1"
    assert str(parsed[0]["cumulative_nav"]) == "1"
    assert parsed[0]["instrument_code"] == "ANZ73A(A级)"
    assert parsed[0]["instrument_name"] == "盈怀香柏树1号私募证券投资基金A类"


def test_parse_nav_rows_from_xlsx_supports_yuan_per_share_headers() -> None:
    rows = [
        ["产品基金净值数据"],
        ["产品代码", "产品名称", "净值日期", "单位净值(元/份)", "累计单位净值(元/份)"],
        ["SAZN63", "孝庸混合策略配置一号私募证券投资基金", "2026-06-01", "1.0147", "1.0147"],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-06-01"
    assert str(parsed[0]["nav"]) == "1.0147"
    assert str(parsed[0]["cumulative_nav"]) == "1.0147"
    assert parsed[0]["instrument_code"] == "SAZN63"
    assert parsed[0]["instrument_name"] == "孝庸混合策略配置一号私募证券投资基金"


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
    assert parsed[0]["instrument_code"] == "ARE77A"
    assert parsed[0]["instrument_name"] == "盈怀香柏树7号私募证券投资基金A"
    assert str(parsed[0]["nav"]) == "1.0"
    assert str(parsed[0]["cumulative_nav"]) == "1.0"


def test_parse_nav_rows_from_xlsx_supports_yyyymmdd_business_dates() -> None:
    rows = [
        [
            "产品代码",
            "产品名称",
            "业务日期",
            "客户资产净值",
            "客户资产份额",
            "单位净值",
            "累计单位净值",
        ],
        [
            "ZB945A",
            "润洲正行11号私募证券投资基金A",
            "20260422",
            "1,028,139.61",
            "1,059,391.66",
            "0.9705",
            "1.5268",
        ],
    ]

    parsed = _parse_nav_rows_from_xlsx(_workbook_bytes(rows))

    assert len(parsed) == 1
    assert parsed[0]["as_of_date"] == "2026-04-22"
    assert parsed[0]["instrument_code"] == "ZB945A"
    assert parsed[0]["instrument_name"] == "润洲正行11号私募证券投资基金A"
    assert str(parsed[0]["nav"]) == "0.9705"
    assert str(parsed[0]["cumulative_nav"]) == "1.5268"


def test_filter_rows_for_rule_supports_exact_code_match() -> None:
    rows = [
        {
            "as_of_date": "2026-04-14",
            "instrument_code": "SAZB60",
            "instrument_name": "九慕稳健配置私募证券投资基金",
            "nav": "1.0657",
            "nav_with_dividend": "1.0657",
        },
        {
            "as_of_date": "2026-04-14",
            "instrument_code": "AZB60A",
            "instrument_name": "九慕稳健配置私募证券投资基金A",
            "nav": "1.0657",
            "nav_with_dividend": "1.0657",
        },
    ]

    filtered = _filter_rows_for_rule(
        rule={"row_code_equals": ["SAZB60"]},
        rows=rows,
    )

    assert len(filtered) == 1
    assert filtered[0]["instrument_code"] == "SAZB60"


def test_reinvested_total_return_correction_uses_existing_anchor(monkeypatch) -> None:
    def fake_get_instrument(instrument_id: str) -> dict[str, object]:
        assert instrument_id == "savf63"
        return {
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                        "as_of_date": "2026-05-27",
                        "value": "1.0000",
                        "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                        "as_of_date": "2026-05-27",
                        "value": "1.1000",
                        "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "cumulative_nav",
                        "as_of_date": "2026-05-27",
                        "value": "1.0500",
                        "status": "complete",
                },
            ]
        }

    monkeypatch.setattr(market_data_ops, "get_instrument", fake_get_instrument)

    rows, applied = _apply_reinvested_total_return_correction(
        instrument_id="savf63",
        rows=[
            {
                "as_of_date": "2026-05-27",
                "nav": "1.0000",
                "cumulative_nav": "1.0500",
            },
            {
                "as_of_date": "2026-05-28",
                "nav": "0.9000",
                "cumulative_nav": "1.1000",
            },
        ],
    )

    assert applied is True
    assert str(rows[0]["nav_with_dividend"]) == "1.1000000000000000"
    assert str(rows[1]["nav_with_dividend"]) == "1.1550000000000000"


def test_reinvested_total_return_correction_keeps_full_precision_between_dividends(monkeypatch) -> None:
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda instrument_id: None)

    rows, applied = _apply_reinvested_total_return_correction(
        instrument_id="savf63",
        rows=[
            {
                "as_of_date": "2026-03-25",
                "nav": "0.9976",
                "cumulative_nav": "1.0295",
            },
            {
                "as_of_date": "2026-03-26",
                "nav": "0.9922",
                "cumulative_nav": "1.0241",
            },
            {
                "as_of_date": "2026-03-27",
                "nav": "0.9937",
                "cumulative_nav": "1.0256",
            },
        ],
    )

    assert applied is True
    assert str(rows[1]["nav_with_dividend"]) == "1.0239273255813953"
    assert str(rows[2]["nav_with_dividend"]) == "1.0254752906976744"


def test_reinvested_total_return_correction_ignores_rounding_noise(monkeypatch) -> None:
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda instrument_id: None)

    rows, applied = _apply_reinvested_total_return_correction(
        instrument_id="savf63",
        rows=[
            {
                "as_of_date": "2026-05-27",
                "nav": "1.0000",
                "cumulative_nav": "1.0500",
            },
            {
                "as_of_date": "2026-05-28",
                "nav": "1.0100",
                "cumulative_nav": "1.0601",
            },
        ],
    )

    assert applied is True
    assert str(rows[1]["nav_with_dividend"]) == "1.0605000000000000"


def test_reinvested_total_return_correction_leaves_other_instruments_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda instrument_id: None)

    rows, applied = _apply_reinvested_total_return_correction(
        instrument_id="sbcj69",
        rows=[
            {
                "as_of_date": "2026-05-28",
                "nav": "1.0000",
                "nav_with_dividend": "1.0500",
            }
        ],
    )

    assert applied is False
    assert rows[0]["nav_with_dividend"] == "1.0500"


def test_incremental_email_import_uses_prior_rows_as_dividend_context(monkeypatch) -> None:
    source_settings = {
        "source_email_rules": [
            {
                "sender_equals": ["yywbfa@cmschina.com.cn"],
                "subject_contains": ["九慕稳健3号"],
                "attachment_name_contains": ["九慕稳健3号"],
                "attachment_extensions": ["xlsx"],
                "row_code_equals": ["SAVF63"],
            }
        ]
    }
    header = ["产品代码", "产品名称", "净值日期", "单位净值", "累计净值"]
    messages = {
        1: _nav_email_bytes(
            subject="九慕稳健3号净值序列",
            attachment_name="九慕稳健3号净值序列.xlsx",
            rows=[
                header,
                ["SAVF63", "九慕稳健3号私募证券投资基金", "20260324", "1.0218", "1.0218"],
                ["SAVF63", "九慕稳健3号私募证券投资基金", "20260325", "0.9976", "1.0295"],
                ["SAVF63", "九慕稳健3号私募证券投资基金", "20260326", "0.9922", "1.0241"],
            ],
        )
    }
    captured: dict[str, object] = {}

    def fake_replace_nav_history(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(
        market_data_ops,
        "get_instrument",
        lambda instrument_id: {
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                        "as_of_date": "2026-03-25",
                        "value": "0.9976",
                        "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                        "as_of_date": "2026-03-25",
                        "value": "1.0295",
                        "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "cumulative_nav",
                        "as_of_date": "2026-03-25",
                        "value": "1.0295",
                        "status": "complete",
                },
            ]
        },
    )
    monkeypatch.setattr(market_data_ops, "replace_nav_history", fake_replace_nav_history)

    record = _import_rows_from_email_rules(
        instrument_id="savf63",
        instrument_currency="CNY",
        rules=_normalized_email_rules(source_settings),
        mailbox=_FakeMailbox(messages),
        pending_uids=[1],
        updated_by="test",
        full_history=False,
        nav_since_date=market_data_ops.date(2026, 3, 25),
    )

    assert record == {"instrument_id": "savf63"}
    rows = captured["rows"]
    assert [row["as_of_date"] for row in rows] == ["2026-03-26"]
    assert str(rows[0]["nav_with_dividend"]) == "1.0239273255813953"
    assert (
        captured["message"]
        == "Imported 1 NAV rows from 1 recent email attachments since 2026-03-25. Recalculated total_return_nav using dividend reinvestment."
    )


def test_incremental_email_import_requires_reinvested_anchor_row(monkeypatch) -> None:
    source_settings = {
        "source_email_rules": [
            {
                "sender_equals": ["yywbfa@cmschina.com.cn"],
                "subject_contains": ["九慕稳健3号"],
                "attachment_name_contains": ["九慕稳健3号"],
                "attachment_extensions": ["xlsx"],
                "row_code_equals": ["SAVF63"],
            }
        ]
    }
    header = ["产品代码", "产品名称", "净值日期", "单位净值", "累计净值"]
    messages = {
        1: _nav_email_bytes(
            subject="九慕稳健3号净值序列",
            attachment_name="九慕稳健3号净值序列.xlsx",
            rows=[
                header,
                ["SAVF63", "九慕稳健3号私募证券投资基金", "20260326", "0.9922", "1.0241"],
            ],
        )
    }
    captured: dict[str, object] = {}

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"instrument_id": kwargs["instrument_id"], "refresh_status": kwargs["status"]}

    def fail_replace_nav_history(**_: object) -> dict[str, object]:
        raise AssertionError("replace_nav_history should not run without a reinvestment anchor")

    monkeypatch.setattr(market_data_ops, "replace_nav_history", fail_replace_nav_history)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)
    monkeypatch.setattr(market_data_ops, "_existing_nav_history_by_date", lambda _instrument_id: {})

    record = _import_rows_from_email_rules(
        instrument_id="savf63",
        instrument_currency="CNY",
        rules=_normalized_email_rules(source_settings),
        mailbox=_FakeMailbox(messages),
        pending_uids=[1],
        updated_by="test",
        full_history=False,
        nav_since_date=market_data_ops.date(2026, 3, 25),
    )

    assert record == {"instrument_id": "savf63", "refresh_status": "blocked"}
    assert captured["message"] == (
        "Email NAV import needs the latest existing NAV date 2026-03-25 in the matched "
        "attachment rows before dividend reinvestment can be recalculated."
    )


def test_incremental_email_import_uses_attachment_nav_dates_not_latest_received_uid(
    monkeypatch,
) -> None:
    source_settings = {
        "source_email_rules": [
            {
                "sender_equals": ["yywbfa@cmschina.com.cn"],
                "subject_contains": ["润洲正行11号私募证券投资基金a", "虚拟计提净值表"],
                "attachment_name_contains": ["润洲正行11号私募证券投资基金a", "虚拟计提后净值表"],
                "attachment_extensions": ["xlsx"],
                "row_code_equals": ["ZB945A"],
                "row_name_equals": ["润洲正行11号私募证券投资基金A"],
            }
        ]
    }
    header = ["产品代码", "产品名称", "业务日期", "单位净值", "累计单位净值"]
    messages = {
        1: _nav_email_bytes(
            subject="润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_虚拟计提净值表_20260403",
            attachment_name="20260403_润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
            rows=[
                header,
                ["ZB945A", "润洲正行11号私募证券投资基金A", "20260403", "0.9352", "1.4915"],
            ],
        ),
        2: _nav_email_bytes(
            subject="润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_虚拟计提净值表_20260402",
            attachment_name="20260402_润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
            rows=[
                header,
                ["ZB945A", "润洲正行11号私募证券投资基金A", "20260402", "0.9334", "1.4897"],
            ],
        ),
    }
    captured: dict[str, object] = {}

    def fake_replace_nav_history(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"instrument_id": kwargs["instrument_id"]}

    def fake_get_instrument(instrument_id: str) -> dict[str, object]:
        assert instrument_id == "zb945a"
        return _zb945a_existing_nav_instrument()

    monkeypatch.setattr(market_data_ops, "get_instrument", fake_get_instrument)
    monkeypatch.setattr(market_data_ops, "replace_nav_history", fake_replace_nav_history)

    record = _import_rows_from_email_rules(
        instrument_id="zb945a",
        instrument_currency="CNY",
        rules=_normalized_email_rules(source_settings),
        mailbox=_FakeMailbox(messages),
        pending_uids=[1, 2],
        updated_by="test",
        full_history=False,
    )

    assert record == {"instrument_id": "zb945a"}
    rows = captured["rows"]
    assert [row["as_of_date"] for row in rows] == ["2026-04-02", "2026-04-03"]
    assert captured["provider"] == "email:recent_window"
    assert (
        captured["message"]
        == "Imported 2 NAV rows from 2 recent email attachments. Recalculated total_return_nav using dividend reinvestment."
    )


def test_email_refresh_uses_success_cursor_and_does_not_reimport_unchanged_latest_date(
    monkeypatch,
) -> None:
    source_settings = {
        "source_email_rules": [
            {
                "sender_equals": ["yywbfa@cmschina.com.cn"],
                "subject_contains": ["润洲正行11号私募证券投资基金a", "虚拟计提净值表"],
                "attachment_name_contains": ["润洲正行11号私募证券投资基金a", "虚拟计提后净值表"],
                "attachment_extensions": ["xlsx"],
                "row_code_equals": ["ZB945A"],
                "row_name_equals": ["润洲正行11号私募证券投资基金A"],
            }
        ]
    }
    header = ["产品代码", "产品名称", "业务日期", "单位净值", "累计单位净值"]
    messages = {
        1: _nav_email_bytes(
            subject="润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_虚拟计提净值表_20260402",
            attachment_name="20260402_润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
            rows=[
                header,
                ["ZB945A", "润洲正行11号私募证券投资基金A", "20260402", "0.9334", "1.4897"],
            ],
        ),
        2: _nav_email_bytes(
            subject="润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_虚拟计提净值表_20260403",
            attachment_name="20260403_润洲正行11号私募证券投资基金A_九慕稳健3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
            rows=[
                header,
                ["ZB945A", "润洲正行11号私募证券投资基金A", "20260403", "0.9352", "1.4915"],
            ],
        ),
    }
    mailboxes: list[_FakeMailbox] = []

    class FakeSettings:
        email_sync_enabled = True
        email_imap_host = "imap.example.test"
        email_imap_port = 993
        email_imap_username = "nav-sync@example.test"
        email_imap_password = "secret"
        email_imap_folder = "INBOX"
        email_imap_use_ssl = True
        email_imap_timeout_seconds = 60
        email_imap_max_messages = 500
        email_imap_mark_seen = False

        @property
        def email_sync_ready(self) -> bool:
            return True

    class FakeRefreshMailbox(_FakeMailbox):
        def __init__(self, host: str, port: int, timeout: int) -> None:
            del host, port, timeout
            super().__init__(messages)
            self.search_calls: list[tuple[object, ...]] = []
            mailboxes.append(self)

        def login(self, username: str, password: str) -> tuple[str, list[object]]:
            del username, password
            return "OK", []

        def select(self, folder: str, readonly: bool = True) -> tuple[str, list[object]]:
            del folder, readonly
            return "OK", []

        def close(self) -> tuple[str, list[object]]:
            return "OK", []

        def logout(self) -> tuple[str, list[object]]:
            return "OK", []

        def uid(self, command: str, *args: object) -> tuple[str, list[object]]:
            if command == "search":
                self.search_calls.append(args)
            return super().uid(command, *args)

    captured: dict[str, object] = {}

    def fake_replace_nav_history(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"instrument_id": kwargs["instrument_id"]}

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        captured.update({"refresh_status": kwargs})
        return {"instrument_id": kwargs["instrument_id"]}

    def fake_get_instrument(instrument_id: str) -> dict[str, object]:
        assert instrument_id == "zb945a"
        return _zb945a_existing_nav_instrument()

    monkeypatch.setattr(market_data_ops, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(market_data_ops, "get_instrument", fake_get_instrument)
    monkeypatch.setattr(market_data_ops.imaplib, "IMAP4_SSL", FakeRefreshMailbox)
    monkeypatch.setattr(market_data_ops, "replace_nav_history", fake_replace_nav_history)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)

    record = market_data_ops._refresh_from_email(
        instrument_id="zb945a",
        instrument={
            "currency": "CNY",
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-04-03",
                    "value": "0.9352",
                    "currency": "CNY",
                    "provider": "email:previous",
                    "status": "complete",
                }
            ],
                "refresh_status": {
                    "status": "imported",
                    "requested_at": "2026-04-10T12:00:00Z",
                    "last_successful_requested_at": "2026-04-10T12:00:00Z",
                },
        },
        source_settings=source_settings,
        updated_by="test",
        full_history=False,
    )

    assert record == {"instrument_id": "zb945a"}
    assert mailboxes[0].search_calls[0] == (None, "SINCE", "09-Apr-2026")
    assert mailboxes[0].search_calls[1] == (
        None,
        "FROM",
        "yywbfa@cmschina.com.cn",
        "SINCE",
        "09-Apr-2026",
    )
    assert "rows" not in captured
    assert captured["refresh_status"]["status"] == "no_new_data"
    assert captured["refresh_status"]["message"] == (
        "Matched email NAV rows, but none were newer or changed since 2026-04-03."
    )


def test_email_search_cursor_does_not_advance_after_failed_blocked_or_non_email_refresh() -> None:
    nav_since_date = market_data_ops.date(2026, 4, 3)

    for status, mode in (("failed", "email"), ("blocked", "email"), ("imported", "manual")):
        assert market_data_ops._email_search_since_date(
            instrument={
                "refresh_status": {
                    "status": status,
                    "mode": mode,
                    "requested_at": "2026-04-10T12:00:00Z",
                }
            },
            nav_since_date=nav_since_date,
            full_history=False,
        ) == market_data_ops.date(2026, 4, 3)

    assert market_data_ops._email_search_since_date(
        instrument={
            "refresh_status": {
                "status": "failed",
                "mode": "email",
                "requested_at": "2026-07-11T12:00:00Z",
                "last_successful_requested_at": "2026-07-09T13:00:00Z",
            }
        },
        nav_since_date=nav_since_date,
        full_history=False,
    ) == market_data_ops.date(2026, 7, 8)


def test_incremental_email_import_replaces_changed_latest_date(monkeypatch) -> None:
    messages = {
        1: _nav_email_bytes(
            subject="CTA因子组合2号净值",
            attachment_name="CTA因子组合2号净值.xlsx",
            rows=[
                ["产品代码", "产品名称", "净值日期", "单位净值", "累计净值"],
                ["SBCJ69", "国泰君安期货CTA因子组合2号", "20260403", "1.1002", "1.1002"],
            ],
        )
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        market_data_ops,
        "get_instrument",
        lambda instrument_id: {
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-04-03",
                    "value": "1.0999",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": "2026-04-03",
                    "value": "1.0999",
                },
            ]
        },
    )
    monkeypatch.setattr(
        market_data_ops,
        "replace_nav_history",
        lambda **kwargs: captured.update(kwargs) or {"instrument_id": kwargs["instrument_id"]},
    )

    record = _import_rows_from_email_rules(
        instrument_id="sbcj69",
        instrument_currency="CNY",
        rules=_normalized_email_rules(
            {
                "source_email_rules": [
                    {
                        "sender_equals": ["yywbfa@cmschina.com.cn"],
                        "subject_contains": ["cta因子组合2号"],
                        "attachment_name_contains": ["cta因子组合2号"],
                        "attachment_extensions": ["xlsx"],
                        "row_code_equals": ["SBCJ69"],
                    }
                ]
            }
        ),
        mailbox=_FakeMailbox(messages),
        pending_uids=[1],
        updated_by="test",
        full_history=False,
        nav_since_date=market_data_ops.date(2026, 4, 3),
    )

    assert record == {"instrument_id": "sbcj69"}
    assert [row["as_of_date"] for row in captured["rows"]] == ["2026-04-03"]
    assert captured["refresh_status"] == "imported"


def test_email_batch_reuses_one_login_and_logout_across_folders(monkeypatch) -> None:
    class FakeSettings:
        email_sync_enabled = True
        email_imap_host = "imap.example.test"
        email_imap_port = 993
        email_imap_username = "nav-sync@example.test"
        email_imap_password = "secret"
        email_imap_folder = "INBOX"
        email_imap_use_ssl = True
        email_imap_timeout_seconds = 60
        email_imap_max_messages = 500
        email_imap_mark_seen = False
        market_data_batch_item_timeout_seconds = 0

        @property
        def email_sync_ready(self) -> bool:
            return True

    instances: list[object] = []

    class FakeBatchMailbox:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            del host, port, timeout
            self.login_count = 0
            self.logout_count = 0
            self.close_count = 0
            self.selected_folders: list[str] = []
            instances.append(self)

        def login(self, username: str, password: str) -> tuple[str, list[object]]:
            del username, password
            self.login_count += 1
            return "OK", []

        def select(self, folder: str, readonly: bool = True) -> tuple[str, list[object]]:
            del readonly
            self.selected_folders.append(folder)
            return "OK", []

        def uid(self, command: str, *args: object) -> tuple[str, list[bytes]]:
            del command, args
            return "OK", [b""]

        def close(self) -> tuple[str, list[object]]:
            self.close_count += 1
            return "OK", []

        def logout(self) -> tuple[str, list[object]]:
            self.logout_count += 1
            return "OK", []

    def instrument(instrument_id: str, folder: str) -> dict[str, object]:
        return {
            "instrument_id": instrument_id,
            "instrument_name": f"Instrument {instrument_id}",
            "instrument_type": "fund",
            "currency": "CNY",
            "market_data": [],
            "source_settings": {
                "source_mode": "email",
                "source_location": folder,
                "source_email_rules": [{}],
            },
            "refresh_status": {"status": "idle"},
        }

    instruments = {
        "email-a": instrument("email-a", "FOLDER-A"),
        "email-b": instrument("email-b", "FOLDER-B"),
    }

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        record = dict(instruments[str(kwargs["instrument_id"])])
        record["refresh_status"] = {
            "status": kwargs["status"],
            "message": kwargs["message"],
        }
        return record

    monkeypatch.setattr(market_data_ops, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(market_data_ops, "list_instruments", lambda **kwargs: list(instruments.values()))
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda instrument_id: instruments[instrument_id])
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)
    monkeypatch.setattr(market_data_ops.imaplib, "IMAP4_SSL", FakeBatchMailbox)

    result = market_data_ops.refresh_market_data_batch(
        source="email",
        updated_by="test",
    )

    assert [item["status"] for item in result["results"]] == ["no_match", "no_match"]
    assert len(instances) == 1
    mailbox = instances[0]
    assert mailbox.login_count == 1
    assert mailbox.selected_folders == ["FOLDER-A", "FOLDER-B", "FOLDER-A", "FOLDER-B"]
    assert mailbox.close_count == 1
    assert mailbox.logout_count == 1


def test_email_batch_reconnects_after_imap_abort(monkeypatch) -> None:
    class FakeSettings:
        email_sync_enabled = True
        email_imap_host = "imap.example.test"
        email_imap_port = 993
        email_imap_username = "nav-sync@example.test"
        email_imap_password = "secret"
        email_imap_folder = "INBOX"
        email_imap_use_ssl = True
        email_imap_timeout_seconds = 60
        email_imap_max_messages = 500
        email_imap_mark_seen = False
        market_data_batch_item_timeout_seconds = 0

        @property
        def email_sync_ready(self) -> bool:
            return True

    mailboxes: list[object] = []

    class FlakyMailbox:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            del host, port, timeout
            self.instance_index = len(mailboxes)
            self.logout_count = 0
            mailboxes.append(self)

        def login(self, username: str, password: str) -> tuple[str, list[object]]:
            del username, password
            return "OK", []

        def select(self, folder: str, readonly: bool = True) -> tuple[str, list[object]]:
            del folder, readonly
            return "OK", []

        def uid(self, command: str, *args: object) -> tuple[str, list[bytes]]:
            del command, args
            if self.instance_index == 0:
                raise market_data_ops.imaplib.IMAP4.abort("connection dropped")
            return "OK", [b""]

        def close(self) -> tuple[str, list[object]]:
            return "OK", []

        def logout(self) -> tuple[str, list[object]]:
            self.logout_count += 1
            return "OK", []

    instrument = {
        "instrument_id": "email-a",
        "instrument_name": "Instrument A",
        "instrument_type": "fund",
        "currency": "CNY",
        "market_data": [],
        "source_settings": {
            "source_mode": "email",
            "source_location": "INBOX",
            "source_email_rules": [{}],
        },
        "refresh_status": {"status": "idle"},
    }

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        return {
            **instrument,
            "refresh_status": {
                "status": kwargs["status"],
                "message": kwargs["message"],
            },
        }

    monkeypatch.setattr(market_data_ops, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(market_data_ops, "list_instruments", lambda **kwargs: [instrument])
    monkeypatch.setattr(market_data_ops, "get_instrument", lambda instrument_id: instrument)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)
    monkeypatch.setattr(market_data_ops.imaplib, "IMAP4_SSL", FlakyMailbox)

    result = market_data_ops.refresh_market_data_batch(source="email", updated_by="test")

    assert result["results"][0]["status"] == "no_match"
    assert len(mailboxes) == 2
    assert mailboxes[0].logout_count == 1
    assert mailboxes[1].logout_count == 1


def test_public_item_timeout_uses_configured_limit_and_returns_failed_status(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSettings:
        market_data_batch_item_timeout_seconds = 17

    class ImmediateTimeout:
        def __init__(self, seconds: int, instrument_id: str) -> None:
            captured["seconds"] = seconds
            captured["instrument_id"] = instrument_id

        def __enter__(self) -> None:
            raise market_data_ops.MarketDataItemTimeout(
                "Market data refresh timed out after 17 seconds for fund-a."
            )

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    def fail_unwrapped_refresh(**kwargs: object) -> dict[str, object]:
        raise AssertionError(f"unwrapped refresh unexpectedly called: {kwargs}")

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        captured["refresh_status"] = kwargs
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(market_data_ops, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(market_data_ops, "_BatchItemTimeout", ImmediateTimeout)
    monkeypatch.setattr(market_data_ops, "refresh_market_data", fail_unwrapped_refresh)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)

    record = market_data_ops.refresh_market_data_with_timeout(
        instrument_id="fund-a",
        updated_by="pytest",
        source="tushare",
    )

    assert record == {"instrument_id": "fund-a"}
    assert captured["seconds"] == 17
    assert captured["instrument_id"] == "fund-a"
    assert captured["refresh_status"] == {
        "instrument_id": "fund-a",
        "status": "failed",
        "message": "Market data refresh timed out after 17 seconds for fund-a.",
        "updated_by": "pytest",
        "mode": "api",
    }


def test_tushare_api_uses_sdk_with_configured_proxy_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSettings:
        tushare_ready = True
        tushare_token = "secret-token"
        tushare_api_url = "https://fastapic.stockai888.top"

    class FakeFrame:
        def to_dict(self, orient: str) -> list[dict[str, object]]:
            assert orient == "records"
            return [{"ts_code": "000300.SH", "trade_date": "20260615", "close": "4200.12"}]

    class FakePro:
        def __init__(self) -> None:
            self._DataApi__http_url = ""

        def index_daily(self, **kwargs: object) -> FakeFrame:
            captured["http_url"] = self._DataApi__http_url
            captured["index_daily_kwargs"] = kwargs
            return FakeFrame()

    class FakeTushareModule:
        def __init__(self) -> None:
            self.pro = FakePro()

        def pro_api(self, token: str, timeout: int) -> FakePro:
            captured["token"] = token
            captured["timeout"] = timeout
            return self.pro

    monkeypatch.setattr(market_data_ops, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(market_data_ops, "ts", FakeTushareModule())

    rows = market_data_ops._call_tushare_api(
        api_name="index_daily",
        params={"ts_code": "000300.SH"},
        fields="ts_code,trade_date,close",
    )

    assert captured["token"] == "secret-token"
    assert captured["timeout"] == 30
    assert captured["http_url"] == "https://fastapic.stockai888.top"
    assert captured["index_daily_kwargs"] == {
        "ts_code": "000300.SH",
        "fields": "ts_code,trade_date,close",
    }
    assert rows == [{"ts_code": "000300.SH", "trade_date": "20260615", "close": "4200.12"}]


def test_tushare_refresh_imports_public_fund_nav(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_tushare_api(**kwargs: object) -> list[dict[str, object]]:
        captured["api_call"] = kwargs
        return [
            {
                "ts_code": "018654.OF",
                "ann_date": "20260616",
                "end_date": None,
                "nav_date": "20260615",
                "unit_nav": "1.2345",
                "accum_nav": "1.3456",
                "adj_nav": "1.4567",
            },
            {
                "ts_code": "018654.OF",
                "ann_date": "20260613",
                "end_date": "20260612",
                "nav_date": "20260612",
                "unit_nav": "1.2000",
                "accum_nav": "1.3000",
                "adj_nav": "1.4000",
            },
        ]

    def fake_replace_nav_history(**kwargs: object) -> dict[str, object]:
        captured["replace"] = kwargs
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(market_data_ops, "_call_tushare_api", fake_call_tushare_api)
    monkeypatch.setattr(
        market_data_ops,
        "get_instrument",
        lambda instrument_id: {
            "instrument_id": instrument_id,
            "currency": "CNY",
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-06-12",
                    "value": "1.2000",
                    "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "cumulative_nav",
                    "as_of_date": "2026-06-12",
                    "value": "1.3000",
                    "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": "2026-06-12",
                    "value": "1.4000",
                    "status": "complete",
                },
            ],
        },
    )
    monkeypatch.setattr(market_data_ops, "replace_nav_history", fake_replace_nav_history)

    record = market_data_ops._refresh_from_tushare(
        instrument_id="018654-of",
        instrument={
            "instrument_id": "018654-of",
            "instrument_type": "fund",
            "currency": "CNY",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "018654.OF", "is_primary": True}
            ],
            "market_data": [
                {
                    "metric_family": "nav",
                    "quote_basis": "official_nav",
                    "as_of_date": "2026-06-12",
                    "value": "1.2000",
                    "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "cumulative_nav",
                    "as_of_date": "2026-06-12",
                    "value": "1.3000",
                    "status": "complete",
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": "2026-06-12",
                    "value": "1.4000",
                    "status": "complete",
                },
            ],
        },
        updated_by="test",
        full_history=False,
    )

    assert record == {"instrument_id": "018654-of"}
    assert captured["api_call"]["api_name"] == "fund_nav"
    assert captured["api_call"]["params"] == {"ts_code": "018654.OF"}
    assert captured["api_call"]["fields"] == "ts_code,ann_date,end_date,nav_date,unit_nav,accum_nav,adj_nav,update_flag"
    replace_payload = captured["replace"]
    assert replace_payload["provider"] == "tushare:fund_nav"
    assert replace_payload["mode"] == "api"
    assert replace_payload["rows"] == [
        {
            "as_of_date": "2026-06-15",
            "nav": market_data_ops.Decimal("1.2345"),
            "cumulative_nav": market_data_ops.Decimal("1.3456"),
            "nav_with_dividend": market_data_ops.Decimal("1.4567"),
            "currency": "CNY",
            "frequency": "daily",
        }
    ]


def test_tushare_refresh_imports_index_close(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_tushare_api(**kwargs: object) -> list[dict[str, object]]:
        captured["api_call"] = kwargs
        return [
            {"ts_code": "000300.SH", "trade_date": "20260615", "close": "4200.12"},
            {"ts_code": "000300.SH", "trade_date": "20260612", "close": "4190.00"},
        ]

    def fake_upsert_market_data_points(**kwargs: object) -> int:
        captured["upsert"] = kwargs
        return len(kwargs["rows"])

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        captured["refresh_status"] = kwargs
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(market_data_ops, "_call_tushare_api", fake_call_tushare_api)
    monkeypatch.setattr(
        market_data_ops,
        "upsert_market_data_points",
        fake_upsert_market_data_points,
    )
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)

    record = market_data_ops._refresh_from_tushare(
        instrument_id="000300-sh",
        instrument={
            "instrument_id": "000300-sh",
            "instrument_type": "index",
            "currency": "CNY",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "000300.SH", "is_primary": True}
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-06-12",
                    "value": "4190.00",
                    "status": "complete",
                }
            ],
        },
        updated_by="test",
        full_history=False,
    )

    assert record == {"instrument_id": "000300-sh"}
    assert captured["api_call"]["api_name"] == "index_daily"
    assert captured["api_call"]["params"]["ts_code"] == "000300.SH"
    assert captured["api_call"]["params"]["start_date"] == "20260613"
    assert captured["upsert"] == {
        "instrument_id": "000300-sh",
        "rows": [
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": market_data_ops.date(2026, 6, 15),
                "value": market_data_ops.Decimal("4200.12"),
                "currency": "CNY",
                "provider": "tushare:index_daily",
                "status": "complete",
            }
        ],
    }
    assert captured["refresh_status"]["status"] == "refreshed"
    assert captured["refresh_status"]["mode"] == "api"


def test_tushare_price_refresh_starts_at_2024_when_no_existing_history(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_tushare_api(**kwargs: object) -> list[dict[str, object]]:
        captured.setdefault("api_calls", []).append(kwargs)
        if kwargs["api_name"] == "fund_adj":
            return [
                {"ts_code": "513050.SH", "trade_date": "20240102", "adj_factor": "1.0"},
            ]
        return [
            {"ts_code": "513050.SH", "trade_date": "20231229", "close": "0.90"},
            {"ts_code": "513050.SH", "trade_date": "20240102", "close": "0.91"},
        ]

    def fake_upsert_market_data_points(**kwargs: object) -> int:
        captured["upsert"] = kwargs
        return len(kwargs["rows"])

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        captured["refresh_status"] = kwargs
        return {"instrument_id": kwargs["instrument_id"]}

    def fake_upsert_quote_selection_policy(**kwargs: object) -> dict[str, object]:
        captured["quote_selection_policy"] = kwargs
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(market_data_ops, "_call_tushare_api", fake_call_tushare_api)
    monkeypatch.setattr(
        market_data_ops,
        "upsert_market_data_points",
        fake_upsert_market_data_points,
    )
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)
    monkeypatch.setattr(
        market_data_ops,
        "upsert_quote_selection_policy",
        fake_upsert_quote_selection_policy,
    )

    market_data_ops._refresh_from_tushare(
        instrument_id="513050-sh",
        instrument={
            "instrument_id": "513050-sh",
            "instrument_type": "fund",
            "currency": "CNY",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "513050.SH", "is_primary": True}
            ],
            "market_data": [],
        },
        updated_by="test",
        full_history=False,
    )

    assert captured["api_calls"][0]["api_name"] == "fund_daily"
    assert captured["api_calls"][0]["params"]["start_date"] == "20240101"
    assert captured["quote_selection_policy"] == {
        "instrument_id": "513050-sh",
        "quote_selection_policy": {
            "trading": ["last", "close"],
            "valuation": ["close", "last"],
            "total_return": ["adjusted_close", "close", "last"],
            "chart": ["adjusted_close", "close", "last"],
            "reference": ["close", "last"],
        },
    }
    assert captured["upsert"]["instrument_id"] == "513050-sh"
    assert captured["upsert"]["rows"] == [
        {
            "metric_family": "price",
            "quote_basis": "close",
            "as_of_date": market_data_ops.date(2024, 1, 2),
            "value": market_data_ops.Decimal("0.91"),
            "currency": "CNY",
            "provider": "tushare:fund_daily",
            "status": "complete",
        },
        {
            "metric_family": "price",
            "quote_basis": "adjusted_close",
            "as_of_date": market_data_ops.date(2024, 1, 2),
            "value": "0.91",
            "currency": "CNY",
            "provider": "tushare:fund_adj:qfq:latest_factor=1",
            "status": "complete",
        },
    ]


def test_tushare_listed_security_never_persists_complete_close_without_adjusted_close(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_call_tushare_api(**kwargs: object) -> list[dict[str, object]]:
        if kwargs["api_name"] == "adj_factor":
            return [
                {"ts_code": "600000.SH", "trade_date": "20260714", "adj_factor": "1"},
            ]
        return [
            {"ts_code": "600000.SH", "trade_date": "20260714", "close": "10.00"},
            {"ts_code": "600000.SH", "trade_date": "20260715", "close": "10.10"},
        ]

    def fake_upsert_market_data_points(**kwargs: object) -> int:
        captured["upsert"] = kwargs
        return len(kwargs["rows"])

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        captured["refresh_status"] = kwargs
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(market_data_ops, "_call_tushare_api", fake_call_tushare_api)
    monkeypatch.setattr(
        market_data_ops,
        "upsert_market_data_points",
        fake_upsert_market_data_points,
    )
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)

    market_data_ops._refresh_from_tushare(
        instrument_id="600000-sh",
        instrument={
            "instrument_id": "600000-sh",
            "instrument_type": "equity",
            "currency": "CNY",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "600000.SH",
                    "is_primary": True,
                }
            ],
            "market_data": [],
            "quote_selection_policy": {
                "trading": ["last", "close"],
                "valuation": ["close", "last"],
                "total_return": ["adjusted_close", "close", "last"],
                "chart": ["adjusted_close", "close", "last"],
                "reference": ["close", "last"],
            },
        },
        updated_by="test",
        full_history=False,
    )

    points = captured["upsert"]["rows"]
    complete_close_dates = {
        point["as_of_date"]
        for point in points
        if point["quote_basis"] == "close" and point["status"] == "complete"
    }
    adjusted_close_dates = {
        point["as_of_date"]
        for point in points
        if point["quote_basis"] == "adjusted_close" and point["status"] == "complete"
    }
    assert complete_close_dates <= adjusted_close_dates
    assert next(
        point
        for point in points
        if point["quote_basis"] == "close"
        and point["as_of_date"] == market_data_ops.date(2026, 7, 15)
    )["status"] == "partial"
    assert captured["refresh_status"]["status"] == "partial"
    assert "missing adjustment factor" in captured["refresh_status"]["message"]
    assert "2026-07-15" in captured["refresh_status"]["message"]


def test_tushare_share_split_detection_is_review_only_until_issuer_confirmation() -> None:
    candidates = market_data_ops._detect_tushare_share_splits(
        factors={
            market_data_ops.date(2026, 3, 27): market_data_ops.Decimal("1"),
            market_data_ops.date(2026, 3, 30): market_data_ops.Decimal("2"),
        },
        close_by_date={
            market_data_ops.date(2026, 3, 27): market_data_ops.Decimal("1.660"),
            market_data_ops.date(2026, 3, 30): market_data_ops.Decimal("0.852"),
        },
    )

    assert len(candidates) == 1
    assert candidates[0]["effective_date"] == market_data_ops.date(2026, 3, 30)
    assert candidates[0]["new_units"] == "2"
    assert candidates[0]["old_units"] == "1"
    assert candidates[0]["status"] == "detected"
    assert candidates[0]["provenance"]["detection_method"] == "tushare_factor_price_continuity/v1"


def test_tushare_factor_change_without_inverse_raw_price_move_is_not_a_split() -> None:
    candidates = market_data_ops._detect_tushare_share_splits(
        factors={
            market_data_ops.date(2026, 3, 27): market_data_ops.Decimal("1"),
            market_data_ops.date(2026, 3, 30): market_data_ops.Decimal("2"),
        },
        close_by_date={
            market_data_ops.date(2026, 3, 27): market_data_ops.Decimal("1.00"),
            market_data_ops.date(2026, 3, 30): market_data_ops.Decimal("1.01"),
        },
    )

    assert candidates == []


def test_tushare_full_history_is_capped_at_2024(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_call_tushare_api(**kwargs: object) -> list[dict[str, object]]:
        captured["api_call"] = kwargs
        return []

    def fake_update_refresh_status(**kwargs: object) -> dict[str, object]:
        captured["refresh_status"] = kwargs
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(market_data_ops, "_call_tushare_api", fake_call_tushare_api)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)

    market_data_ops._refresh_from_tushare(
        instrument_id="000300-sh",
        instrument={
            "instrument_id": "000300-sh",
            "instrument_type": "index",
            "currency": "CNY",
            "identifiers": [
                {"identifier_type": "ticker", "identifier_value": "000300.SH", "is_primary": True}
            ],
            "market_data": [
                {
                    "metric_family": "price",
                    "quote_basis": "close",
                    "as_of_date": "2026-06-12",
                    "value": "4190.00",
                    "status": "complete",
                }
            ],
        },
        updated_by="test",
        full_history=True,
    )

    assert captured["api_call"]["api_name"] == "index_daily"
    assert captured["api_call"]["params"]["start_date"] == "20240101"


def test_parse_nav_rows_from_label_snapshot_matrix_extracts_nav_values() -> None:
    matrix = [
        ["招商证券股份有限公司_九慕稳健1号私募证券投资基金_专用表", None],
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
    assert str(parsed[0]["cumulative_nav"]) == "1.1135"
    assert "currency" not in parsed[0]
    assert parsed[0]["frequency"] == "daily"

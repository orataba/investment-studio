from __future__ import annotations

from email.message import EmailMessage
from email import policy
from io import BytesIO
import zipfile

from openpyxl import Workbook

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
    assert parsed[0]["instrument_code"] == "SBCJ69"
    assert parsed[0]["instrument_name"] == "国泰君安期货CTA因子组合2号集合资产管理计划"
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
    assert parsed[0]["instrument_code"] == "ANZ73A(A级)"
    assert parsed[0]["instrument_name"] == "盈怀香柏树1号私募证券投资基金A类"


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
    assert str(parsed[0]["nav_with_dividend"]) == "1.0"


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
    assert str(parsed[0]["nav_with_dividend"]) == "1.5268"


def test_filter_rows_for_rule_supports_exact_code_match() -> None:
    rows = [
        {
            "as_of_date": "2026-04-14",
            "instrument_code": "SAZB60",
            "instrument_name": "九慕云谷均衡配置私募证券投资基金",
            "nav": "1.0657",
            "nav_with_dividend": "1.0657",
        },
        {
            "as_of_date": "2026-04-14",
            "instrument_code": "AZB60A",
            "instrument_name": "九慕云谷均衡配置私募证券投资基金A",
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
                },
                {
                    "metric_family": "nav",
                    "quote_basis": "total_return_nav",
                    "as_of_date": "2026-05-27",
                    "value": "1.1000",
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
                "nav_with_dividend": "1.0500",
            },
            {
                "as_of_date": "2026-05-28",
                "nav": "0.9000",
                "nav_with_dividend": "1.1000",
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
                "nav_with_dividend": "1.0295",
            },
            {
                "as_of_date": "2026-03-26",
                "nav": "0.9922",
                "nav_with_dividend": "1.0241",
            },
            {
                "as_of_date": "2026-03-27",
                "nav": "0.9937",
                "nav_with_dividend": "1.0256",
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
                "nav_with_dividend": "1.0500",
            },
            {
                "as_of_date": "2026-05-28",
                "nav": "1.0100",
                "nav_with_dividend": "1.0601",
            },
        ],
    )

    assert applied is True
    assert str(rows[1]["nav_with_dividend"]) == "1.0605000000000000"


def test_reinvested_total_return_correction_leaves_other_instruments_unchanged() -> None:
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
                "subject_contains": ["九慕云谷3号"],
                "attachment_name_contains": ["九慕云谷3号"],
                "attachment_extensions": ["xlsx"],
                "row_code_equals": ["SAVF63"],
            }
        ]
    }
    header = ["产品代码", "产品名称", "净值日期", "单位净值", "累计净值"]
    messages = {
        1: _nav_email_bytes(
            subject="九慕云谷3号净值序列",
            attachment_name="九慕云谷3号净值序列.xlsx",
            rows=[
                header,
                ["SAVF63", "九慕云谷3号私募证券投资基金", "20260324", "1.0218", "1.0218"],
                ["SAVF63", "九慕云谷3号私募证券投资基金", "20260325", "0.9976", "1.0295"],
                ["SAVF63", "九慕云谷3号私募证券投资基金", "20260326", "0.9922", "1.0241"],
            ],
        )
    }
    captured: dict[str, object] = {}

    def fake_replace_nav_history(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"instrument_id": kwargs["instrument_id"]}

    monkeypatch.setattr(market_data_ops, "get_instrument", lambda instrument_id: None)
    monkeypatch.setattr(market_data_ops, "replace_nav_history", fake_replace_nav_history)

    record = _import_rows_from_email_rules(
        instrument_id="savf63",
        rules=_normalized_email_rules(source_settings),
        mailbox=_FakeMailbox(messages),
        pending_uids=[1],
        updated_by="test",
        full_history=False,
        nav_since_date=market_data_ops.date(2026, 3, 26),
    )

    assert record == {"instrument_id": "savf63"}
    rows = captured["rows"]
    assert [row["as_of_date"] for row in rows] == ["2026-03-26"]
    assert str(rows[0]["nav_with_dividend"]) == "1.0239273255813953"
    assert (
        captured["message"]
        == "Imported 1 NAV rows from 1 recent email attachments since 2026-03-26. Recalculated total_return_nav using dividend reinvestment."
    )


def test_incremental_email_import_requires_reinvested_anchor_row(monkeypatch) -> None:
    source_settings = {
        "source_email_rules": [
            {
                "sender_equals": ["yywbfa@cmschina.com.cn"],
                "subject_contains": ["九慕云谷3号"],
                "attachment_name_contains": ["九慕云谷3号"],
                "attachment_extensions": ["xlsx"],
                "row_code_equals": ["SAVF63"],
            }
        ]
    }
    header = ["产品代码", "产品名称", "净值日期", "单位净值", "累计净值"]
    messages = {
        1: _nav_email_bytes(
            subject="九慕云谷3号净值序列",
            attachment_name="九慕云谷3号净值序列.xlsx",
            rows=[
                header,
                ["SAVF63", "九慕云谷3号私募证券投资基金", "20260326", "0.9922", "1.0241"],
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

    record = _import_rows_from_email_rules(
        instrument_id="savf63",
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
            subject="润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_虚拟计提净值表_20260403",
            attachment_name="20260403_润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
            rows=[
                header,
                ["ZB945A", "润洲正行11号私募证券投资基金A", "20260403", "0.9352", "1.4915"],
            ],
        ),
        2: _nav_email_bytes(
            subject="润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_虚拟计提净值表_20260402",
            attachment_name="20260402_润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
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

    monkeypatch.setattr(market_data_ops, "replace_nav_history", fake_replace_nav_history)

    record = _import_rows_from_email_rules(
        instrument_id="zb945a",
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


def test_email_refresh_searches_since_latest_nav_date_and_filters_older_rows(
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
            subject="润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_虚拟计提净值表_20260402",
            attachment_name="20260402_润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
            rows=[
                header,
                ["ZB945A", "润洲正行11号私募证券投资基金A", "20260402", "0.9334", "1.4897"],
            ],
        ),
        2: _nav_email_bytes(
            subject="润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_虚拟计提净值表_20260403",
            attachment_name="20260403_润洲正行11号私募证券投资基金A_九慕云谷3号私募证券投资基金_TA虚拟计提后净值表.xlsx",
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

    monkeypatch.setattr(market_data_ops, "get_settings", lambda: FakeSettings())
    monkeypatch.setattr(market_data_ops.imaplib, "IMAP4_SSL", FakeRefreshMailbox)
    monkeypatch.setattr(market_data_ops, "replace_nav_history", fake_replace_nav_history)
    monkeypatch.setattr(market_data_ops, "update_refresh_status", fake_update_refresh_status)

    record = market_data_ops._refresh_from_email(
        instrument_id="zb945a",
        instrument={
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
            ]
        },
        source_settings=source_settings,
        updated_by="test",
        full_history=False,
    )

    assert record == {"instrument_id": "zb945a"}
    assert mailboxes[0].search_calls[0] == (None, "SINCE", "03-Apr-2026")
    assert mailboxes[0].search_calls[1] == (
        None,
        "FROM",
        "yywbfa@cmschina.com.cn",
        "SINCE",
        "03-Apr-2026",
    )
    rows = captured["rows"]
    assert [row["as_of_date"] for row in rows] == ["2026-04-03"]
    assert (
        captured["message"]
        == "Imported 1 NAV rows from 1 recent email attachments since 2026-04-03. Recalculated total_return_nav using dividend reinvestment."
    )


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

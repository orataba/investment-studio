from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from io import BytesIO
import imaplib
import logging
import re
import socket
from typing import Any

from platform_app.core.settings import get_settings
from platform_app.services.instrument_store import (
    get_instrument,
    list_instruments,
    replace_nav_history,
    update_refresh_status,
    upsert_quote_selection_policy,
    upsert_market_data,
)

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - optional dependency
    load_workbook = None

try:
    import xlrd
except ImportError:  # pragma: no cover - optional dependency
    xlrd = None

try:
    import tushare as ts
except ImportError:  # pragma: no cover - optional dependency
    ts = None


LOGGER = logging.getLogger("portfolio_ops.market_data_ops")

NAV_IMPORT_HEADER_MAP = {
    "date": "as_of_date",
    "日期": "as_of_date",
    "净值日期": "as_of_date",
    "业务日期": "as_of_date",
    "交易日期": "as_of_date",
    "估值基准日": "as_of_date",
    "asofdate": "as_of_date",
    "as_of_date": "as_of_date",
    "trade_date": "as_of_date",
    "navdate": "as_of_date",
    "nav": "nav",
    "净值": "nav",
    "单位净值": "nav",
    "单位净值元": "nav",
    "单位净值元份": "nav",
    "资产份额净值元": "nav",
    "实际净值": "nav",
    "navwithdividend": "nav_with_dividend",
    "nav_with_dividend": "nav_with_dividend",
    "累计净值": "nav_with_dividend",
    "累计净值元": "nav_with_dividend",
    "累计单位净值": "nav_with_dividend",
    "累计单位净值元": "nav_with_dividend",
    "累计单位净值元份": "nav_with_dividend",
    "资产份额累计净值元": "nav_with_dividend",
    "实际累计净值": "nav_with_dividend",
    "currency": "currency",
    "ccy": "currency",
    "币种": "currency",
    "frequency": "frequency",
    "freq": "frequency",
    "频率": "frequency",
    "productcode": "instrument_code",
    "产品代码": "instrument_code",
    "产品编码": "instrument_code",
    "instrumentcode": "instrument_code",
    "资产代码": "instrument_code",
    "fundcode": "instrument_code",
    "基金代码": "instrument_code",
    "productname": "instrument_name",
    "产品名称": "instrument_name",
    "instrumentname": "instrument_name",
    "资产名称": "instrument_name",
    "fundname": "instrument_name",
    "基金名称": "instrument_name",
}

LABEL_SNAPSHOT_FIELD_ALIASES = {
    "as_of_date": ("日期", "净值日期"),
    "nav": ("单位净值",),
    "nav_with_dividend": ("累计单位净值",),
}

REINVESTED_TOTAL_RETURN_INSTRUMENT_IDS = {
    "anz73a",
    "bvk42b",
    "savf63",
    "sgs754",
    "yunsheng-shicheng-arbitrage-1-b",
    "zb945a",
}

TOTAL_RETURN_NAV_DECIMAL_PLACES = Decimal("0.0000000000000001")
CASH_DISTRIBUTION_EVENT_THRESHOLD = Decimal("0.0001")
TUSHARE_PROFILE_ALIASES = {"tushare", "tushare_pro", "tushare-pro"}
TUSHARE_PRICE_SUFFIXES = {"SH", "SZ"}
TUSHARE_INDEX_SUFFIXES = {"SH", "SZ", "CSI", "CNI"}
TUSHARE_HISTORY_START_DATE = date(2024, 1, 1)
TUSHARE_LISTED_FUND_QUOTE_SELECTION_POLICY: dict[str, list[str]] = {
    "trading": ["close", "last", "official_nav"],
    "valuation": ["close", "last", "official_nav"],
    "total_return": ["close", "adjusted_close", "total_return_nav", "official_nav"],
    "chart": ["close", "adjusted_close", "total_return_nav", "official_nav"],
    "reference": ["close", "last", "official_nav"],
}


def _is_reinvested_total_return_instrument(instrument_id: str) -> bool:
    return str(instrument_id or "").strip().lower() in REINVESTED_TOTAL_RETURN_INSTRUMENT_IDS


def _normalize_nav_header(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())


def _excel_serial_to_date(value: float) -> date | None:
    if value <= 0:
        return None
    try:
        return (datetime(1899, 12, 30) + timedelta(days=value)).date()
    except OverflowError:
        return None


def _parse_nav_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _excel_serial_to_date(float(value))

    normalized = value.strip()
    if not normalized:
        return None
    if re.fullmatch(r"\d{8}", normalized):
        try:
            return datetime.strptime(normalized, "%Y%m%d").date()
        except ValueError:
            pass
    if re.fullmatch(r"\d+(?:\.\d+)?", normalized):
        return _excel_serial_to_date(float(normalized))
    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%m/%d/%Y",
        "%m-%d-%Y",
        "%d/%m/%Y",
        "%Y%m%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y年%m月%d日",
        "%Y年%m月%d日 %H:%M:%S",
        "%Y年%m月%d日 %H:%M",
    ):
        try:
            return datetime.strptime(normalized, fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(normalized)
    except ValueError:
        return None


def _parse_nav_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    normalized = str(value).strip().replace(",", "")
    if not normalized:
        return None
    try:
        return Decimal(normalized)
    except Exception:
        return None


def _format_total_return_nav_decimal(value: Decimal) -> Decimal:
    return value.quantize(TOTAL_RETURN_NAV_DECIMAL_PLACES, rounding=ROUND_HALF_UP)


def _detect_delimiter(line: str) -> str:
    if "\t" in line:
        return "\t"
    if line.count(";") > line.count(","):
        return ";"
    return ","


def _split_delimited_line(line: str, delimiter: str) -> list[str]:
    return [cell.strip().strip('"') for cell in line.split(delimiter)]


def _matrix_from_text(raw_text: str) -> list[list[object]]:
    lines = [line.strip() for line in raw_text.replace("\r\n", "\n").split("\n") if line.strip()]
    if not lines:
        return []
    delimiter = _detect_delimiter(lines[0])
    return [_split_delimited_line(line, delimiter) for line in lines]


def _matrix_from_xlsx(file_bytes: bytes) -> list[list[object]]:
    if load_workbook is None:
        return []
    workbook = load_workbook(BytesIO(file_bytes), data_only=True, read_only=False)
    for sheet in workbook.worksheets:
        matrix = [list(row) for row in sheet.iter_rows(values_only=True)]
        if any(any(value is not None and str(value).strip() for value in row) for row in matrix):
            return matrix
    return []


def _matrix_from_xls(file_bytes: bytes) -> list[list[object]]:
    if xlrd is None:
        return []
    workbook = xlrd.open_workbook(file_contents=file_bytes)
    for index in range(workbook.nsheets):
        sheet = workbook.sheet_by_index(index)
        matrix = [sheet.row_values(row_index) for row_index in range(sheet.nrows)]
        if any(any(str(value).strip() for value in row) for row in matrix):
            return matrix
    return []


def _extract_attachment_text_from_matrix(matrix: list[list[object]]) -> str:
    parts: list[str] = []
    for row in matrix:
        for value in row:
            text = "" if value is None else str(value).strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _extract_numeric_from_text(label: str, value: str) -> Decimal | None:
    normalized_label = label.strip()
    candidates = [
        value,
        value.replace("：", ":"),
    ]
    for candidate in candidates:
        if normalized_label and normalized_label in candidate:
            tail = candidate.split(normalized_label, 1)[-1].lstrip(":： ")
            parsed = _parse_nav_decimal(tail)
            if parsed is not None:
                return parsed
    return None


def _parse_nav_rows_from_matrix(matrix: list[list[object]]) -> list[dict[str, object]]:
    if not matrix:
        return []
    header: list[str] | None = None
    start_index = 0
    for idx, row in enumerate(matrix[:12]):
        mapped = [NAV_IMPORT_HEADER_MAP.get(_normalize_nav_header(str(cell)), "") for cell in row]
        if "as_of_date" in mapped and any(key in mapped for key in ("nav", "nav_with_dividend")):
            header = mapped
            start_index = idx + 1
            break
    if header is None:
        return []

    rows: list[dict[str, object]] = []
    for raw_row in matrix[start_index:]:
        values = ["" if value is None else str(value).strip() for value in raw_row]
        if not any(values):
            continue
        row_data: dict[str, object] = {
            "currency": "CNY",
            "frequency": "daily",
        }
        for index, column in enumerate(header):
            if not column:
                continue
            cell = values[index] if index < len(values) else ""
            if column == "as_of_date":
                parsed_date = _parse_nav_date(cell)
                if parsed_date is not None:
                    row_data["as_of_date"] = parsed_date.isoformat()
            elif column in {"nav", "nav_with_dividend"}:
                parsed_value = _parse_nav_decimal(cell)
                if parsed_value is not None:
                    row_data[column] = parsed_value
            elif column == "currency" and cell:
                row_data["currency"] = cell.upper()
            elif column == "frequency" and cell:
                row_data["frequency"] = cell.lower()
            elif column in {"instrument_code", "instrument_name"} and cell:
                row_data[column] = cell
        if row_data.get("as_of_date") and any(
            row_data.get(key) is not None for key in ("nav", "nav_with_dividend")
        ):
            rows.append(row_data)
    rows.sort(key=lambda item: str(item["as_of_date"]))
    return rows


def _parse_nav_rows_from_label_snapshot_matrix(matrix: list[list[object]]) -> list[dict[str, object]]:
    if not matrix:
        return []

    found_date: date | None = None
    found_nav: Decimal | None = None
    found_total_return_nav: Decimal | None = None
    normalized_aliases = {
        field: tuple(_normalize_nav_header(alias) for alias in aliases)
        for field, aliases in LABEL_SNAPSHOT_FIELD_ALIASES.items()
    }

    for row in matrix:
        string_values = ["" if value is None else str(value).strip() for value in row]
        if not any(string_values):
            continue
        for index, cell in enumerate(string_values):
            normalized_cell = _normalize_nav_header(cell)
            next_value = string_values[index + 1] if index + 1 < len(string_values) else ""

            if found_date is None and normalized_cell in normalized_aliases["as_of_date"]:
                found_date = _parse_nav_date(next_value)
            if found_nav is None and normalized_cell in normalized_aliases["nav"]:
                found_nav = _parse_nav_decimal(next_value)
            if (
                found_total_return_nav is None
                and normalized_cell in normalized_aliases["nav_with_dividend"]
            ):
                found_total_return_nav = _parse_nav_decimal(next_value)

            if found_date is None and any(alias in cell for alias in LABEL_SNAPSHOT_FIELD_ALIASES["as_of_date"]):
                found_date = _parse_nav_date(
                    re.sub(r"^.*?[：:]\s*", "", cell).strip()
                )
            if found_nav is None:
                found_nav = _extract_numeric_from_text("单位净值", cell)
            if found_total_return_nav is None:
                found_total_return_nav = _extract_numeric_from_text("累计单位净值", cell)

    if found_date is None or found_nav is None:
        return []

    row: dict[str, object] = {
        "as_of_date": found_date.isoformat(),
        "nav": found_nav,
        "currency": "CNY",
        "frequency": "daily",
    }
    if found_total_return_nav is not None:
        row["nav_with_dividend"] = found_total_return_nav
    return [row]


def _parse_nav_rows_from_text(raw_text: str) -> list[dict[str, object]]:
    return _parse_nav_rows_from_matrix(_matrix_from_text(raw_text))


def _parse_nav_rows_from_xlsx(file_bytes: bytes) -> list[dict[str, object]]:
    return _parse_nav_rows_from_matrix(_matrix_from_xlsx(file_bytes))


def _parse_nav_rows_from_xls(file_bytes: bytes) -> list[dict[str, object]]:
    return _parse_nav_rows_from_matrix(_matrix_from_xls(file_bytes))


def _parse_nav_rows_from_uploaded_file(
    *,
    file_name: str,
    file_bytes: bytes,
) -> list[dict[str, object]]:
    lower_name = file_name.lower().strip()
    if lower_name.endswith((".csv", ".tsv", ".txt")):
        return _parse_nav_rows_from_text(file_bytes.decode("utf-8", errors="ignore"))
    if lower_name.endswith(".xlsx"):
        return _parse_nav_rows_from_xlsx(file_bytes)
    if lower_name.endswith(".xls"):
        return _parse_nav_rows_from_xls(file_bytes)
    raise ValueError("Unsupported NAV file type. Use csv, tsv, txt, xlsx, or xls.")


def _parse_nav_rows_from_attachment(
    *,
    attachment_name: str,
    attachment_bytes: bytes,
    parser_profile: str,
) -> list[dict[str, object]]:
    lower_name = attachment_name.lower()
    if parser_profile == "label_nav_snapshot":
        if lower_name.endswith(".xls"):
            return _parse_nav_rows_from_label_snapshot_matrix(_matrix_from_xls(attachment_bytes))
        if lower_name.endswith(".xlsx"):
            return _parse_nav_rows_from_label_snapshot_matrix(_matrix_from_xlsx(attachment_bytes))
        return []

    if lower_name.endswith((".csv", ".tsv", ".txt")):
        return _parse_nav_rows_from_text(attachment_bytes.decode("utf-8", errors="ignore"))
    if lower_name.endswith(".xlsx"):
        return _parse_nav_rows_from_xlsx(attachment_bytes)
    if lower_name.endswith(".xls"):
        return _parse_nav_rows_from_xls(attachment_bytes)
    return []


def _extract_attachment_text(attachment_name: str, attachment_bytes: bytes) -> str:
    lower_name = attachment_name.lower()
    if lower_name.endswith((".csv", ".tsv", ".txt")):
        return attachment_bytes.decode("utf-8", errors="ignore")
    if lower_name.endswith(".xlsx"):
        return _extract_attachment_text_from_matrix(_matrix_from_xlsx(attachment_bytes))
    if lower_name.endswith(".xls"):
        return _extract_attachment_text_from_matrix(_matrix_from_xls(attachment_bytes))
    return ""


def _extract_email_body_text(message) -> str:
    text_parts: list[str] = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() != "text/plain":
            continue
        try:
            content = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            charset = part.get_content_charset() or "utf-8"
            content = payload.decode(charset, errors="ignore")
        if content:
            text_parts.append(str(content))
    return "\n".join(text_parts)


def _fetch_message_bytes(mailbox, uid: int, request: str) -> bytes | None:
    fetch_status, fetch_data = mailbox.uid("fetch", str(uid), request)
    if fetch_status != "OK":
        return None
    return next(
        (
            bytes(item[1])
            for item in fetch_data
            if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray))
        ),
        None,
    )


def _extract_email_attachment_candidates(message) -> list[tuple[str, bytes]]:
    attachments: list[tuple[str, bytes]] = []
    for part in message.walk():
        filename = part.get_filename()
        if not filename and part.get_content_disposition() != "attachment":
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        attachments.append((filename or "attachment", payload))
    return attachments


def _normalize_text_token(value: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", value.lower())


def _normalized_email_rules(source_settings: dict[str, object]) -> list[dict[str, object]]:
    raw_rules = source_settings.get("source_email_rules", [])
    if not isinstance(raw_rules, list):
        return []
    rules: list[dict[str, object]] = []
    for rule in raw_rules:
        if not isinstance(rule, dict):
            continue
        normalized = {
            "sender_equals": [str(item).strip().lower() for item in rule.get("sender_equals", []) if str(item).strip()],
            "subject_contains": [str(item).strip().lower() for item in rule.get("subject_contains", []) if str(item).strip()],
            "subject_excludes": [str(item).strip().lower() for item in rule.get("subject_excludes", []) if str(item).strip()],
            "attachment_name_contains": [
                str(item).strip().lower() for item in rule.get("attachment_name_contains", []) if str(item).strip()
            ],
            "attachment_name_excludes": [
                str(item).strip().lower() for item in rule.get("attachment_name_excludes", []) if str(item).strip()
            ],
            "attachment_extensions": [
                str(item).strip().lower().lstrip(".")
                for item in rule.get("attachment_extensions", [])
                if str(item).strip()
            ],
            "attachment_content_contains": [
                str(item).strip().lower()
                for item in rule.get("attachment_content_contains", [])
                if str(item).strip()
            ],
            "row_code_equals": [
                str(item).strip().upper()
                for item in rule.get("row_code_equals", [])
                if str(item).strip()
            ],
            "row_name_equals": [
                _normalize_text_token(str(item))
                for item in rule.get("row_name_equals", [])
                if str(item).strip()
            ],
            "row_name_contains": [
                _normalize_text_token(str(item))
                for item in rule.get("row_name_contains", [])
                if str(item).strip()
            ],
            "row_name_excludes": [
                _normalize_text_token(str(item))
                for item in rule.get("row_name_excludes", [])
                if str(item).strip()
            ],
            "parser_profile": str(rule.get("parser_profile") or "generic_nav_table").strip() or "generic_nav_table",
        }
        rules.append(normalized)
    return rules


def _row_matches_rule(
    *,
    rule: dict[str, object],
    row: dict[str, object],
) -> bool:
    row_code = str(row.get("instrument_code") or "").strip().upper()
    row_name = _normalize_text_token(str(row.get("instrument_name") or ""))

    required_codes = list(rule.get("row_code_equals", []))
    if required_codes and row_code not in required_codes:
        return False

    required_name_equals = list(rule.get("row_name_equals", []))
    if required_name_equals and row_name not in required_name_equals:
        return False

    required_name_contains = list(rule.get("row_name_contains", []))
    if required_name_contains and not any(token and token in row_name for token in required_name_contains):
        return False

    excluded_name_tokens = list(rule.get("row_name_excludes", []))
    if any(token and token in row_name for token in excluded_name_tokens):
        return False

    return True


def _filter_rows_for_rule(
    *,
    rule: dict[str, object],
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not rows:
        return []
    has_row_selectors = any(
        list(rule.get(key, []))
        for key in ("row_code_equals", "row_name_equals", "row_name_contains", "row_name_excludes")
    )
    if not has_row_selectors:
        return rows
    return [row for row in rows if _row_matches_rule(rule=rule, row=row)]


def _message_matches_rule(
    *,
    rule: dict[str, object],
    subject: str,
    sender_email: str,
) -> bool:
    normalized_subject = subject.lower()
    sender_equals = list(rule.get("sender_equals", []))
    if sender_equals and sender_email not in sender_equals:
        return False
    if any(keyword not in normalized_subject for keyword in list(rule.get("subject_contains", []))):
        return False
    if any(keyword in normalized_subject for keyword in list(rule.get("subject_excludes", []))):
        return False
    return True


def _attachment_matches_rule(
    *,
    rule: dict[str, object],
    attachment_name: str,
    attachment_text: str,
) -> bool:
    normalized_name = attachment_name.lower()
    normalized_text = attachment_text.lower()
    required_extensions = list(rule.get("attachment_extensions", []))
    if required_extensions:
        extension = normalized_name.rsplit(".", 1)[-1] if "." in normalized_name else ""
        if extension not in required_extensions:
            return False
    if any(keyword not in normalized_name for keyword in list(rule.get("attachment_name_contains", []))):
        return False
    if any(keyword in normalized_name for keyword in list(rule.get("attachment_name_excludes", []))):
        return False
    if any(keyword not in normalized_text for keyword in list(rule.get("attachment_content_contains", []))):
        return False
    return True


def _normalize_import_status(rows: list[dict[str, object]], fallback: str) -> str:
    if any(row.get("nav") is None or row.get("nav_with_dividend") is None for row in rows):
        return "partial"
    return fallback


def _merge_rows_by_date(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for row in rows:
        as_of_date = str(row.get("as_of_date") or "").strip()
        if not as_of_date:
            continue
        merged[as_of_date] = row
    return [merged[key] for key in sorted(merged.keys())]


def _existing_nav_history_by_date(instrument_id: str) -> dict[date, dict[str, Decimal]]:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        return {}

    history: dict[date, dict[str, Decimal]] = {}
    for point in list(instrument.get("market_data", [])):
        if not isinstance(point, dict):
            continue
        if str(point.get("metric_family") or "").strip() != "nav":
            continue
        point_date = _parse_nav_date(point.get("as_of_date"))
        point_value = _parse_nav_decimal(point.get("value"))
        quote_basis = str(point.get("quote_basis") or "").strip()
        if point_date is None or point_value is None:
            continue
        if quote_basis == "official_nav":
            history.setdefault(point_date, {})["nav"] = point_value
        elif quote_basis == "total_return_nav":
            history.setdefault(point_date, {})["nav_with_dividend"] = point_value
    return history


def _apply_reinvested_total_return_correction(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], bool]:
    if not _is_reinvested_total_return_instrument(instrument_id) or not rows:
        return rows, False

    existing_history = _existing_nav_history_by_date(instrument_id)
    corrected_rows: list[dict[str, object]] = []
    recognized_cash_distribution: Decimal | None = None
    reinvested_factor: Decimal | None = None
    applied = False

    for row in sorted(rows, key=lambda item: str(item.get("as_of_date") or "")):
        corrected_row = dict(row)
        row_date = _parse_nav_date(row.get("as_of_date"))
        row_nav = _parse_nav_decimal(row.get("nav"))
        row_cash_total_nav = _parse_nav_decimal(row.get("nav_with_dividend"))
        if row_date is None or row_nav is None or row_cash_total_nav is None:
            corrected_rows.append(corrected_row)
            continue

        existing_same_day = existing_history.get(row_date, {})
        existing_same_day_total = existing_same_day.get("nav_with_dividend")
        cash_distribution = row_cash_total_nav - row_nav
        if reinvested_factor is None:
            reinvested_total_nav = existing_same_day_total or row_cash_total_nav
            reinvested_factor = reinvested_total_nav / row_nav if row_nav != 0 else None
            recognized_cash_distribution = cash_distribution
        else:
            cash_dividend = (
                Decimal("0")
                if recognized_cash_distribution is None
                else cash_distribution - recognized_cash_distribution
            )
            if abs(cash_dividend) <= CASH_DISTRIBUTION_EVENT_THRESHOLD:
                cash_dividend = Decimal("0")
            if row_nav == 0 or reinvested_factor is None:
                reinvested_total_nav = row_cash_total_nav
            else:
                if cash_dividend != 0:
                    reinvested_factor = reinvested_factor * (
                        Decimal("1") + cash_dividend / row_nav
                    )
                    recognized_cash_distribution = cash_distribution
                reinvested_total_nav = row_nav * reinvested_factor

        formatted_total_nav = _format_total_return_nav_decimal(reinvested_total_nav)
        if formatted_total_nav != row_cash_total_nav:
            applied = True
        corrected_row["nav_with_dividend"] = formatted_total_nav
        corrected_rows.append(corrected_row)

    return corrected_rows, applied


def _requires_reinvested_incremental_anchor(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
    full_history: bool,
    nav_since_date: date | None,
) -> bool:
    if (
        full_history
        or nav_since_date is None
        or not _is_reinvested_total_return_instrument(instrument_id)
    ):
        return False
    row_dates = {_parse_nav_date(row.get("as_of_date")) for row in rows}
    return nav_since_date not in row_dates


def _filter_rows_for_instrument(
    *,
    instrument: dict[str, object],
    rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    if not rows:
        return []

    has_row_identity = any(
        str(row.get("instrument_code") or "").strip() or str(row.get("instrument_name") or "").strip()
        for row in rows
    )
    if not has_row_identity:
        return rows

    identifier_candidates = {
        str(item.get("identifier_value") or "").strip().upper()
        for item in list(instrument.get("identifiers", []))
        if str(item.get("identifier_value") or "").strip()
    }
    identifier_candidates.add(str(instrument.get("instrument_id") or "").strip().upper())

    instrument_name = _normalize_text_token(str(instrument.get("instrument_name") or ""))
    filtered: list[dict[str, object]] = []
    for row in rows:
        row_code = str(row.get("instrument_code") or "").strip().upper()
        row_name = _normalize_text_token(str(row.get("instrument_name") or ""))
        code_match = bool(row_code) and row_code in identifier_candidates
        name_match = bool(row_name and instrument_name) and (
            row_name == instrument_name
            or row_name in instrument_name
            or instrument_name in row_name
        )
        if code_match or name_match:
            filtered.append(row)
    return filtered


def _prepare_nav_rows_for_instrument(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        return None, []

    filtered_rows = _filter_rows_for_instrument(instrument=instrument, rows=rows)
    if not filtered_rows:
        has_row_identity = any(
            str(row.get("instrument_code") or "").strip()
            or str(row.get("instrument_name") or "").strip()
            for row in rows
        )
        if has_row_identity:
            raise ValueError(
                "Detected NAV rows, but none matched the selected instrument identifier or name."
            )
        raise ValueError("No NAV rows detected.")

    return instrument, _merge_rows_by_date(filtered_rows)


def _search_uids_for_rule(
    mailbox,
    *,
    rule: dict[str, object],
    fallback_uids: list[int],
    search_criteria: tuple[str, ...] = ("ALL",),
) -> list[int]:
    sender_equals = list(rule.get("sender_equals", []))
    if not sender_equals:
        return fallback_uids

    matched: set[int] = set()
    criteria_suffix = () if search_criteria == ("ALL",) else search_criteria
    for sender in sender_equals:
        search_status, search_data = mailbox.uid("search", None, "FROM", sender, *criteria_suffix)
        if search_status != "OK":
            return fallback_uids
        raw_uid_list = search_data[0] if search_data and search_data[0] else b""
        matched.update(int(item) for item in raw_uid_list.split() if item)
    if not matched:
        return []
    fallback_set = set(fallback_uids)
    return [uid for uid in sorted(matched) if uid in fallback_set]


def _latest_nav_date_from_instrument(instrument: dict[str, object]) -> date | None:
    latest_nav_date: date | None = None
    for point in list(instrument.get("market_data", [])):
        if not isinstance(point, dict):
            continue
        if str(point.get("metric_family") or "").strip() != "nav":
            continue
        current_date = _parse_nav_date(point.get("as_of_date"))
        if current_date is None:
            continue
        if latest_nav_date is None or current_date > latest_nav_date:
            latest_nav_date = current_date
    return latest_nav_date


def _latest_market_data_date_from_instrument(
    instrument: dict[str, object],
    *,
    metric_family: str,
    quote_bases: set[str],
) -> date | None:
    latest_date: date | None = None
    for point in list(instrument.get("market_data", [])):
        if not isinstance(point, dict):
            continue
        if str(point.get("metric_family") or "").strip() != metric_family:
            continue
        if str(point.get("quote_basis") or "").strip() not in quote_bases:
            continue
        current_date = _parse_nav_date(point.get("as_of_date"))
        if current_date is None:
            continue
        if latest_date is None or current_date > latest_date:
            latest_date = current_date
    return latest_date


def _format_tushare_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _tushare_query_start_date(*, latest_date: date | None, full_history: bool) -> date:
    if full_history or latest_date is None or latest_date < TUSHARE_HISTORY_START_DATE:
        return TUSHARE_HISTORY_START_DATE
    return latest_date + timedelta(days=1)


def _tushare_profile_enabled(source_settings: dict[str, object]) -> bool:
    profile = str(source_settings.get("source_api_profile") or "").strip().lower()
    return profile in TUSHARE_PROFILE_ALIASES


def _tushare_identifier_code(instrument: dict[str, object]) -> str | None:
    identifiers = [
        item for item in list(instrument.get("identifiers", [])) if isinstance(item, dict)
    ]
    ordered_identifiers = sorted(
        identifiers,
        key=lambda item: (
            0 if bool(item.get("is_primary")) else 1,
            0 if str(item.get("identifier_type") or "").strip().lower() == "ticker" else 1,
        ),
    )
    for identifier in ordered_identifiers:
        raw_value = str(identifier.get("identifier_value") or "").strip().upper()
        if re.fullmatch(r"[0-9A-Z]{5,12}\.(?:OF|SH|SZ|CSI|CNI)", raw_value):
            return raw_value
    return None


def _policy_role_values(policy: dict[str, object], role: str) -> list[str]:
    raw_values = policy.get(role)
    if not isinstance(raw_values, list):
        return []
    values: list[str] = []
    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if value and value not in values:
            values.append(value)
    return values


def _ensure_tushare_listed_fund_quote_policy(
    *,
    instrument_id: str,
    instrument: dict[str, object],
) -> None:
    current_policy = instrument.get("quote_selection_policy", {})
    if not isinstance(current_policy, dict):
        current_policy = {}
    target_policy = {
        role: list(values)
        for role, values in TUSHARE_LISTED_FUND_QUOTE_SELECTION_POLICY.items()
    }
    if all(
        _policy_role_values(current_policy, role) == values
        for role, values in target_policy.items()
    ):
        return
    upsert_quote_selection_policy(
        instrument_id=instrument_id,
        quote_selection_policy=target_policy,
    )


class TushareRefreshError(RuntimeError):
    pass


def _is_tushare_missing_value(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def _call_tushare_api(
    *,
    api_name: str,
    params: dict[str, object],
    fields: str,
) -> list[dict[str, object]]:
    settings = get_settings()
    if not settings.tushare_ready:
        raise TushareRefreshError("Tushare token is not configured. Set PORTFOLIO_OPS_PLATFORM_TUSHARE_TOKEN first.")
    if ts is None:
        raise TushareRefreshError("Tushare SDK is not installed. Install the backend dependency first.")

    previous_socket_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(getattr(settings, "tushare_timeout_seconds", 30))
        ts.set_token(settings.tushare_token)
        pro = ts.pro_api()
        setattr(pro, "_DataApi__http_url", settings.tushare_api_url)
        api_method = getattr(pro, api_name)
        frame = api_method(**params, fields=fields)
    except Exception as exc:
        raise TushareRefreshError(f"Tushare SDK request failed: {exc}") from exc
    finally:
        socket.setdefaulttimeout(previous_socket_timeout)

    if frame is None:
        return []
    to_dict = getattr(frame, "to_dict", None)
    if not callable(to_dict):
        return []
    raw_rows = to_dict("records")
    if not isinstance(raw_rows, list):
        return []

    rows: list[dict[str, object]] = []
    for raw_row in raw_rows:
        if not isinstance(raw_row, dict):
            continue
        row: dict[str, object] = {}
        for key, value in raw_row.items():
            if _is_tushare_missing_value(value):
                row[str(key)] = None
            else:
                row[str(key)] = value
        rows.append(row)
    return rows


def _tushare_nav_rows(
    rows: list[dict[str, object]],
    *,
    latest_date: date | None,
) -> list[dict[str, object]]:
    prepared_rows: list[dict[str, object]] = []
    for row in rows:
        point_date = _parse_nav_date(row.get("nav_date") or row.get("end_date") or row.get("ann_date"))
        if point_date is None:
            continue
        if point_date < TUSHARE_HISTORY_START_DATE:
            continue
        if latest_date is not None and point_date <= latest_date:
            continue
        nav = _parse_nav_decimal(row.get("unit_nav"))
        total_return_nav = _parse_nav_decimal(row.get("accum_nav"))
        if total_return_nav is None:
            total_return_nav = _parse_nav_decimal(row.get("adj_nav"))
        if nav is None and total_return_nav is None:
            continue
        prepared_rows.append(
            {
                "as_of_date": point_date.isoformat(),
                "nav": nav,
                "nav_with_dividend": total_return_nav,
                "currency": "CNY",
                "frequency": "daily",
            }
        )
    return _merge_rows_by_date(prepared_rows)


def _tushare_price_rows(
    rows: list[dict[str, object]],
    *,
    latest_date: date | None,
) -> list[dict[str, object]]:
    prepared_rows: list[dict[str, object]] = []
    for row in rows:
        point_date = _parse_nav_date(row.get("trade_date"))
        if point_date is None:
            continue
        if point_date < TUSHARE_HISTORY_START_DATE:
            continue
        if latest_date is not None and point_date <= latest_date:
            continue
        close_value = _parse_nav_decimal(row.get("close"))
        if close_value is None:
            continue
        prepared_rows.append(
            {
                "as_of_date": point_date,
                "value": close_value,
            }
        )
    return sorted(prepared_rows, key=lambda item: item["as_of_date"])


def _format_imap_since_date(value: date) -> str:
    month = (
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    )[value.month - 1]
    return f"{value.day:02d}-{month}-{value.year}"


def _filter_rows_since_nav_date(
    rows: list[dict[str, object]],
    *,
    nav_since_date: date | None,
) -> list[dict[str, object]]:
    if nav_since_date is None:
        return rows
    filtered_rows: list[dict[str, object]] = []
    for row in rows:
        row_date = _parse_nav_date(row.get("as_of_date"))
        if row_date is not None and row_date >= nav_since_date:
            filtered_rows.append(row)
    return filtered_rows


def _import_rows_from_email_rules(
    *,
    instrument_id: str,
    rules: list[dict[str, object]],
    mailbox,
    pending_uids: list[int],
    updated_by: str | None,
    full_history: bool,
    nav_since_date: date | None = None,
    search_criteria: tuple[str, ...] = ("ALL",),
) -> dict[str, object] | None:
    matched_rows: list[dict[str, object]] = []
    matched_batches: list[tuple[str, str, list[dict[str, object]]]] = []
    ordered_uids = pending_uids

    for rule in rules:
        parser_profile = str(rule.get("parser_profile") or "generic_nav_table")
        rule_uids = _search_uids_for_rule(
            mailbox,
            rule=rule,
            fallback_uids=ordered_uids,
            search_criteria=search_criteria,
        )
        for uid in rule_uids:
            header_bytes = _fetch_message_bytes(
                mailbox,
                uid,
                "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM)])",
            )
            if header_bytes is None:
                continue
            header_message = BytesParser(policy=policy.default).parsebytes(header_bytes)
            subject = str(header_message.get("subject") or "")
            sender = str(header_message.get("from") or "")
            sender_email = parseaddr(sender)[1].strip().lower()
            if not _message_matches_rule(rule=rule, subject=subject, sender_email=sender_email):
                continue
            raw_bytes = _fetch_message_bytes(mailbox, uid, "(RFC822)")
            if raw_bytes is None:
                continue

            message = BytesParser(policy=policy.default).parsebytes(raw_bytes)

            attachment_candidates = _extract_email_attachment_candidates(message)
            for attachment_name, attachment_bytes in attachment_candidates:
                attachment_text = _extract_attachment_text(attachment_name, attachment_bytes)
                if not _attachment_matches_rule(
                    rule=rule,
                    attachment_name=attachment_name,
                    attachment_text=attachment_text,
                ):
                    continue
                rows = _parse_nav_rows_from_attachment(
                    attachment_name=attachment_name,
                    attachment_bytes=attachment_bytes,
                    parser_profile=parser_profile,
                )
                rows = _filter_rows_for_rule(rule=rule, rows=rows)
                if not rows:
                    continue
                matched_rows.extend(rows)
                matched_batches.append((f"{uid}:{attachment_name}", attachment_name, rows))
                break

    merged_rows = _merge_rows_by_date(matched_rows)
    if not merged_rows:
        return None
    if _requires_reinvested_incremental_anchor(
        instrument_id=instrument_id,
        rows=merged_rows,
        full_history=full_history,
        nav_since_date=nav_since_date,
    ):
        return update_refresh_status(
            instrument_id=instrument_id,
            status="blocked",
            message=(
                "Email NAV import needs the latest existing NAV date "
                f"{nav_since_date.isoformat()} in the matched attachment rows before "
                "dividend reinvestment can be recalculated."
            ),
            updated_by=updated_by,
            mode="email",
        )
    merged_rows, reinvested_correction_applied = _apply_reinvested_total_return_correction(
        instrument_id=instrument_id,
        rows=merged_rows,
    )
    if not full_history:
        merged_rows = _filter_rows_since_nav_date(merged_rows, nav_since_date=nav_since_date)
    if not merged_rows:
        return None
    used_provider_refs: list[str] = []
    used_attachment_names: list[str] = []
    for provider_ref, attachment_name, batch_rows in matched_batches:
        used_rows = (
            batch_rows
            if full_history
            else _filter_rows_since_nav_date(batch_rows, nav_since_date=nav_since_date)
        )
        if not used_rows:
            continue
        used_provider_refs.append(provider_ref)
        used_attachment_names.append(attachment_name)

    if full_history:
        provider = "email:history"
        message = (
            f"Imported {len(merged_rows)} NAV rows from {len(used_provider_refs)} "
            "email attachments."
        )
    else:
        unique_attachment_names = list(dict.fromkeys(used_attachment_names))
        provider = (
            f"email:{unique_attachment_names[0]}"
            if len(unique_attachment_names) == 1
            else "email:recent_window"
        )
        since_text = f" since {nav_since_date.isoformat()}" if nav_since_date else ""
        message = (
            f"Imported {len(merged_rows)} NAV rows from {len(used_provider_refs)} "
            f"recent email attachments{since_text}."
        )
    if reinvested_correction_applied:
        message = f"{message} Recalculated total_return_nav using dividend reinvestment."
    return replace_nav_history(
        instrument_id=instrument_id,
        rows=merged_rows,
        provider=provider,
        point_status=_normalize_import_status(merged_rows, "complete"),
        refresh_status="imported",
        updated_by=updated_by,
        message=message,
        mode="email",
    )


def import_nav_text(
    *,
    instrument_id: str,
    raw_text: str,
    provider: str | None,
    status: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    rows = _parse_nav_rows_from_text(raw_text)
    _, prepared_rows = _prepare_nav_rows_for_instrument(instrument_id=instrument_id, rows=rows)
    normalized_status = _normalize_import_status(prepared_rows, status)
    return replace_nav_history(
        instrument_id=instrument_id,
        rows=prepared_rows,
        provider=provider or "platform_manual_import",
        point_status=normalized_status,
        refresh_status="imported",
        updated_by=updated_by,
        message=f"Imported {len(prepared_rows)} NAV rows into shared market data.",
        mode="manual",
    )


def preview_nav_import(
    *,
    instrument_id: str,
    raw_text: str | None = None,
    file_name: str | None = None,
    file_bytes: bytes | None = None,
) -> list[dict[str, object]] | None:
    if raw_text is not None:
        rows = _parse_nav_rows_from_text(raw_text)
    elif file_name is not None and file_bytes is not None:
        rows = _parse_nav_rows_from_uploaded_file(file_name=file_name, file_bytes=file_bytes)
    else:
        raise ValueError("Provide NAV import text or a NAV file payload.")

    instrument, prepared_rows = _prepare_nav_rows_for_instrument(instrument_id=instrument_id, rows=rows)
    if instrument is None:
        return None
    return prepared_rows


def import_nav_file(
    *,
    instrument_id: str,
    file_name: str,
    file_bytes: bytes,
    provider: str | None,
    status: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    rows = _parse_nav_rows_from_uploaded_file(file_name=file_name, file_bytes=file_bytes)
    _, prepared_rows = _prepare_nav_rows_for_instrument(instrument_id=instrument_id, rows=rows)
    normalized_status = _normalize_import_status(prepared_rows, status)
    return replace_nav_history(
        instrument_id=instrument_id,
        rows=prepared_rows,
        provider=provider or f"platform_file_import:{file_name}",
        point_status=normalized_status,
        refresh_status="imported",
        updated_by=updated_by,
        message=f"Imported {len(prepared_rows)} NAV rows from {file_name}.",
        mode="manual",
    )


def refresh_market_data(
    *,
    instrument_id: str,
    updated_by: str | None,
    full_history: bool = False,
    source: str = "configured",
) -> dict[str, object] | None:
    instrument = get_instrument(instrument_id)
    if instrument is None:
        return None

    source_settings = dict(instrument.get("source_settings", {}))
    requested_source = str(source or "configured").strip().lower()
    if requested_source == "email":
        return _refresh_from_email(
            instrument_id=instrument_id,
            instrument=instrument,
            source_settings=source_settings,
            updated_by=updated_by,
            full_history=full_history,
        )
    if requested_source == "tushare":
        return _refresh_from_tushare(
            instrument_id=instrument_id,
            instrument=instrument,
            updated_by=updated_by,
            full_history=full_history,
        )

    source_mode = str(source_settings.get("source_mode") or "manual")
    if source_mode == "email":
        return _refresh_from_email(
            instrument_id=instrument_id,
            instrument=instrument,
            source_settings=source_settings,
            updated_by=updated_by,
            full_history=full_history,
        )
    if source_mode == "api":
        profile = str(source_settings.get("source_api_profile") or "").strip()
        if profile.lower() in TUSHARE_PROFILE_ALIASES:
            return _refresh_from_tushare(
                instrument_id=instrument_id,
                instrument=instrument,
                updated_by=updated_by,
                full_history=full_history,
            )
        return update_refresh_status(
            instrument_id=instrument_id,
            status="blocked",
            message=(
                f"API refresh profile {profile or 'unconfigured profile'} is not wired in Database Dashboard yet."
            ),
            updated_by=updated_by,
            mode="api",
        )

    location = str(source_settings.get("source_location") or "Database Dashboard").strip()
    return update_refresh_status(
        instrument_id=instrument_id,
        status="awaiting_manual_import",
        message=f"Manual source active in Database Dashboard. Import canonical NAV from {location}.",
        updated_by=updated_by,
        mode="manual",
    )


def _refresh_from_tushare(
    *,
    instrument_id: str,
    instrument: dict[str, object],
    updated_by: str | None,
    full_history: bool,
) -> dict[str, object] | None:
    ts_code = _tushare_identifier_code(instrument)
    if ts_code is None:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="blocked",
            message="Tushare refresh requires a Tushare ts_code identifier such as 018654.OF or 000300.SH.",
            updated_by=updated_by,
            mode="api",
        )

    instrument_type = str(instrument.get("instrument_type") or "").strip().lower()
    suffix = ts_code.rsplit(".", 1)[-1]
    try:
        if instrument_type == "fund" and suffix == "OF":
            latest_date = None if full_history else _latest_nav_date_from_instrument(instrument)
            rows = _call_tushare_api(
                api_name="fund_nav",
                params={"ts_code": ts_code},
                fields="ts_code,ann_date,end_date,nav_date,unit_nav,accum_nav,adj_nav,update_flag",
            )
            nav_rows = _tushare_nav_rows(rows, latest_date=latest_date)
            if not nav_rows:
                since_text = f" since {latest_date.isoformat()}" if latest_date else ""
                return update_refresh_status(
                    instrument_id=instrument_id,
                    status="no_new_data",
                    message=f"Tushare fund_nav returned no new NAV rows for {ts_code}{since_text}.",
                    updated_by=updated_by,
                    mode="api",
                )
            return replace_nav_history(
                instrument_id=instrument_id,
                rows=nav_rows,
                provider="tushare:fund_nav",
                point_status="complete",
                refresh_status="imported",
                updated_by=updated_by,
                message=f"Imported {len(nav_rows)} NAV rows from Tushare fund_nav for {ts_code}.",
                mode="api",
            )

        if instrument_type == "fund" and suffix in TUSHARE_PRICE_SUFFIXES:
            _ensure_tushare_listed_fund_quote_policy(
                instrument_id=instrument_id,
                instrument=instrument,
            )
            latest_date = None if full_history else _latest_market_data_date_from_instrument(
                instrument,
                metric_family="price",
                quote_bases={"close"},
            )
            params: dict[str, object] = {"ts_code": ts_code}
            params["start_date"] = _format_tushare_date(
                _tushare_query_start_date(latest_date=latest_date, full_history=full_history)
            )
            params["end_date"] = _format_tushare_date(date.today())
            rows = _call_tushare_api(
                api_name="fund_daily",
                params=params,
                fields="ts_code,trade_date,close",
            )
            return _upsert_tushare_price_rows(
                instrument_id=instrument_id,
                ts_code=ts_code,
                api_name="fund_daily",
                rows=_tushare_price_rows(rows, latest_date=latest_date),
                updated_by=updated_by,
            )

        if instrument_type == "index" and suffix in TUSHARE_INDEX_SUFFIXES:
            latest_date = None if full_history else _latest_market_data_date_from_instrument(
                instrument,
                metric_family="price",
                quote_bases={"close"},
            )
            params = {"ts_code": ts_code}
            params["start_date"] = _format_tushare_date(
                _tushare_query_start_date(latest_date=latest_date, full_history=full_history)
            )
            params["end_date"] = _format_tushare_date(date.today())
            rows = _call_tushare_api(
                api_name="index_daily",
                params=params,
                fields="ts_code,trade_date,close",
            )
            return _upsert_tushare_price_rows(
                instrument_id=instrument_id,
                ts_code=ts_code,
                api_name="index_daily",
                rows=_tushare_price_rows(rows, latest_date=latest_date),
                updated_by=updated_by,
            )
    except TushareRefreshError as exc:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="failed",
            message=f"Tushare refresh failed for {ts_code}: {exc}",
            updated_by=updated_by,
            mode="api",
        )

    return update_refresh_status(
        instrument_id=instrument_id,
        status="blocked",
        message=f"Tushare refresh is not supported for {instrument_type or 'instrument'} code {ts_code}.",
        updated_by=updated_by,
        mode="api",
    )


def _upsert_tushare_price_rows(
    *,
    instrument_id: str,
    ts_code: str,
    api_name: str,
    rows: list[dict[str, object]],
    updated_by: str | None,
) -> dict[str, object] | None:
    if not rows:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=f"Tushare {api_name} returned no new close rows for {ts_code}.",
            updated_by=updated_by,
            mode="api",
        )

    refreshed: dict[str, object] | None = None
    for row in rows:
        refreshed = upsert_market_data(
            instrument_id=instrument_id,
            metric_family="price",
            quote_basis="close",
            as_of_date=row["as_of_date"],
            value=str(row["value"]),
            currency="CNY",
            provider=f"tushare:{api_name}",
            status="complete",
        )
    return update_refresh_status(
        instrument_id=instrument_id,
        status="refreshed",
        message=f"Imported {len(rows)} close rows from Tushare {api_name} for {ts_code}.",
        updated_by=updated_by,
        mode="api",
    ) or refreshed


def _matches_batch_source(instrument: dict[str, object], source: str) -> bool:
    source_settings = dict(instrument.get("source_settings", {}))
    source_mode = str(source_settings.get("source_mode") or "manual").strip().lower()
    normalized_source = source.strip().lower()
    if normalized_source == "email":
        return source_mode == "email"
    if normalized_source == "tushare":
        return source_mode == "api" and _tushare_profile_enabled(source_settings)
    if normalized_source == "all":
        return source_mode == "email" or (source_mode == "api" and _tushare_profile_enabled(source_settings))
    return False


def refresh_market_data_batch(
    *,
    source: str,
    updated_by: str | None,
    full_history: bool = False,
    include_inactive: bool = False,
) -> dict[str, object]:
    normalized_source = str(source or "all").strip().lower()
    if normalized_source == "configured":
        normalized_source = "all"
    instruments = list_instruments(include_inactive=include_inactive)
    targets = [item for item in instruments if _matches_batch_source(item, normalized_source)]
    LOGGER.info(
        "market data batch targets source=%s target_count=%s skipped_count=%s",
        normalized_source,
        len(targets),
        len(instruments) - len(targets),
    )
    results: list[dict[str, object]] = []
    for index, instrument in enumerate(targets, start=1):
        instrument_id = str(instrument.get("instrument_id") or "")
        LOGGER.info(
            "market data batch item started source=%s index=%s/%s instrument_id=%s",
            normalized_source,
            index,
            len(targets),
            instrument_id,
        )
        refreshed = refresh_market_data(
            instrument_id=instrument_id,
            updated_by=updated_by,
            full_history=full_history,
            source="configured",
        )
        if refreshed is None:
            LOGGER.info(
                "market data batch item skipped source=%s index=%s/%s instrument_id=%s",
                normalized_source,
                index,
                len(targets),
                instrument_id,
            )
            continue
        source_settings = dict(refreshed.get("source_settings", {}))
        refresh_status = dict(refreshed.get("refresh_status", {}))
        LOGGER.info(
            "market data batch item finished source=%s index=%s/%s instrument_id=%s status=%s",
            normalized_source,
            index,
            len(targets),
            refreshed["instrument_id"],
            refresh_status.get("status") or "idle",
        )
        results.append(
            {
                "instrument_id": refreshed["instrument_id"],
                "instrument_name": refreshed["instrument_name"],
                "instrument_type": refreshed["instrument_type"],
                "source_mode": source_settings.get("source_mode") or "manual",
                "source_api_profile": source_settings.get("source_api_profile") or "",
                "status": refresh_status.get("status") or "idle",
                "message": refresh_status.get("message") or "",
            }
        )
    return {
        "source": normalized_source,
        "refreshed_count": sum(1 for item in results if item["status"] in {"imported", "refreshed"}),
        "skipped_count": len(instruments) - len(targets),
        "results": results,
    }


def _refresh_from_email(
    *,
    instrument_id: str,
    instrument: dict[str, object],
    source_settings: dict[str, object],
    updated_by: str | None,
    full_history: bool,
) -> dict[str, object] | None:
    settings = get_settings()
    if not settings.email_sync_enabled:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="blocked",
            message="Email refresh is disabled. Set PORTFOLIO_OPS_PLATFORM_EMAIL_SYNC_ENABLED=true first.",
            updated_by=updated_by,
            mode="email",
        )
    if not settings.email_sync_ready:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="blocked",
            message="Email refresh is not configured. Set IMAP host, username, and password.",
            updated_by=updated_by,
            mode="email",
        )

    mailbox: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
    preferred_folder = str(source_settings.get("source_location", "")).strip()
    email_rules = _normalized_email_rules(source_settings)
    if not email_rules:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="blocked",
            message="Email refresh requires at least one explicit product email rule.",
            updated_by=updated_by,
            mode="email",
        )
    try:
        mailbox_cls = imaplib.IMAP4_SSL if settings.email_imap_use_ssl else imaplib.IMAP4
        mailbox = mailbox_cls(
            settings.email_imap_host,
            settings.email_imap_port,
            timeout=settings.email_imap_timeout_seconds,
        )
        login_status, _ = mailbox.login(settings.email_imap_username, settings.email_imap_password)
        if login_status != "OK":
            return update_refresh_status(
                instrument_id=instrument_id,
                status="failed",
                message="Email refresh failed: IMAP login failed.",
                updated_by=updated_by,
                mode="email",
            )

        selected_folder = None
        for folder in [preferred_folder, settings.email_imap_folder]:
            if not folder or folder == selected_folder:
                continue
            select_status, _ = mailbox.select(folder, readonly=not settings.email_imap_mark_seen)
            if select_status == "OK":
                selected_folder = folder
                break
        if selected_folder is None:
            return update_refresh_status(
                instrument_id=instrument_id,
                status="blocked",
                message="Email refresh failed: unable to open the configured mailbox folder.",
                updated_by=updated_by,
                mode="email",
            )

        nav_since_date = None if full_history else _latest_nav_date_from_instrument(instrument)
        search_criteria = (
            ("ALL",)
            if full_history or nav_since_date is None
            else ("SINCE", _format_imap_since_date(nav_since_date))
        )
        search_status, search_data = mailbox.uid("search", None, *search_criteria)
        if search_status != "OK":
            return update_refresh_status(
                instrument_id=instrument_id,
                status="failed",
                message="Email refresh failed: unable to list mailbox messages.",
                updated_by=updated_by,
                mode="email",
            )
        raw_uid_list = search_data[0] if search_data and search_data[0] else b""
        available_uids = [int(item) for item in raw_uid_list.split() if item]
        pending_uids = (
            available_uids
            if full_history or nav_since_date is not None
            else available_uids[-settings.email_imap_max_messages :]
        )

        if email_rules:
            record = _import_rows_from_email_rules(
                instrument_id=instrument_id,
                rules=email_rules,
                mailbox=mailbox,
                pending_uids=pending_uids,
                updated_by=updated_by,
                full_history=full_history,
                nav_since_date=nav_since_date,
                search_criteria=search_criteria,
            )
            if record is not None:
                return record

        since_text = f" since {nav_since_date.isoformat()}" if nav_since_date else ""
        return update_refresh_status(
            instrument_id=instrument_id,
            status="no_match",
            message=f"No email attachment matched the explicit product rules{since_text}.",
            updated_by=updated_by,
            mode="email",
        )
    except Exception as exc:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="failed",
            message=f"Email refresh failed: {exc}",
            updated_by=updated_by,
            mode="email",
        )
    finally:
        if mailbox is not None:
            try:
                mailbox.close()
            except Exception:
                pass
            try:
                mailbox.logout()
            except Exception:
                pass

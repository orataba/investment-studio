from __future__ import annotations

from contextlib import AbstractContextManager
from collections import OrderedDict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from io import BytesIO
import imaplib
import logging
import multiprocessing
import re
import signal
import threading
import time
from typing import Any

from platform_app.core.settings import get_settings
from platform_app.services.instrument_store import (
    get_instrument,
    list_instruments,
    replace_nav_history,
    update_refresh_status,
    upsert_quote_selection_policy,
    upsert_corporate_action_event,
    upsert_market_data_points,
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


class MarketDataItemTimeout(TimeoutError):
    pass


class _BatchItemTimeout:
    def __init__(self, seconds: int, instrument_id: str) -> None:
        self.seconds = max(0, int(seconds or 0))
        self.instrument_id = instrument_id
        self._enabled = (
            self.seconds > 0
            and threading.current_thread() is threading.main_thread()
            and hasattr(signal, "SIGALRM")
            and hasattr(signal, "setitimer")
        )
        self._previous_handler: Any = None
        self._previous_timer: tuple[float, float] = (0.0, 0.0)

    def __enter__(self) -> None:
        if not self._enabled:
            return
        self._previous_handler = signal.getsignal(signal.SIGALRM)
        self._previous_timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, self._raise_timeout)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self._enabled:
            return
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self._previous_handler)
        previous_delay, previous_interval = self._previous_timer
        if previous_delay > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_delay, previous_interval)

    def _raise_timeout(self, signum: int, frame: object) -> None:
        raise MarketDataItemTimeout(
            f"Market data refresh timed out after {self.seconds} seconds for {self.instrument_id}."
        )


def market_data_item_timeout(instrument_id: str) -> AbstractContextManager[None]:
    settings = get_settings()
    return _BatchItemTimeout(settings.market_data_batch_item_timeout_seconds, instrument_id)

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
    "累计净值": "cumulative_nav",
    "累计净值元": "cumulative_nav",
    "累计单位净值": "cumulative_nav",
    "累计单位净值元": "cumulative_nav",
    "累计单位净值元份": "cumulative_nav",
    "资产份额累计净值元": "cumulative_nav",
    "实际累计净值": "cumulative_nav",
    "复权净值": "nav_with_dividend",
    "复权单位净值": "nav_with_dividend",
    "分红再投资净值": "nav_with_dividend",
    "分红再投资单位净值": "nav_with_dividend",
    "复利净值": "nav_with_dividend",
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
    "cumulative_nav": ("累计单位净值",),
    "nav_with_dividend": ("复权单位净值", "分红再投资净值", "复利净值"),
}

TOTAL_RETURN_NAV_DECIMAL_PLACES = Decimal("0.0000000000000001")
CASH_DISTRIBUTION_EVENT_THRESHOLD = Decimal("0.0001")
TUSHARE_PROFILE_ALIASES = {"tushare", "tushare_pro", "tushare-pro"}
TUSHARE_PRICE_SUFFIXES = {"SH", "SZ"}
TUSHARE_INDEX_SUFFIXES = {"SH", "SZ", "CSI", "CNI"}
TUSHARE_HISTORY_START_DATE = date(2024, 1, 1)
TUSHARE_LISTED_SECURITY_QUOTE_SELECTION_POLICY: dict[str, list[str]] = {
    "trading": ["last", "close"],
    "valuation": ["close", "last"],
    "total_return": ["adjusted_close"],
    "chart": ["adjusted_close", "close", "last"],
    "reference": ["close", "last"],
}


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
        if "as_of_date" in mapped and any(
            key in mapped for key in ("nav", "cumulative_nav", "nav_with_dividend")
        ):
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
            elif column in {"nav", "cumulative_nav", "nav_with_dividend"}:
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
            row_data.get(key) is not None
            for key in ("nav", "cumulative_nav", "nav_with_dividend")
        ):
            rows.append(row_data)
    rows.sort(key=lambda item: str(item["as_of_date"]))
    return rows


def _parse_nav_rows_from_label_snapshot_matrix(matrix: list[list[object]]) -> list[dict[str, object]]:
    if not matrix:
        return []

    found_date: date | None = None
    found_nav: Decimal | None = None
    found_cumulative_nav: Decimal | None = None
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
                found_cumulative_nav is None
                and normalized_cell in normalized_aliases["cumulative_nav"]
            ):
                found_cumulative_nav = _parse_nav_decimal(next_value)
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
            if found_cumulative_nav is None:
                found_cumulative_nav = _extract_numeric_from_text("累计单位净值", cell)
            if found_total_return_nav is None:
                for label in ("复权单位净值", "分红再投资净值", "复利净值"):
                    found_total_return_nav = _extract_numeric_from_text(label, cell)
                    if found_total_return_nav is not None:
                        break

    if found_date is None or found_nav is None:
        return []

    row: dict[str, object] = {
        "as_of_date": found_date.isoformat(),
        "nav": found_nav,
        "currency": "CNY",
        "frequency": "daily",
    }
    if found_cumulative_nav is not None:
        row["cumulative_nav"] = found_cumulative_nav
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
    cached_fetch = getattr(mailbox, "fetch_message_bytes", None)
    if callable(cached_fetch):
        return cached_fetch(uid, request)
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
        merged[as_of_date] = {**merged.get(as_of_date, {}), **row}
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
        elif quote_basis in {"cumulative_nav", "accumulated_nav", "cum_nav"}:
            history.setdefault(point_date, {})["cumulative_nav"] = point_value
    return history


def _apply_reinvested_total_return_correction(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], bool]:
    """Derive a reinvested total-return NAV from unit and cash-cumulative NAV.

    `cumulative_nav` is unit NAV plus cash distributions per original share; it
    is not itself a reinvested series. An explicit `nav_with_dividend` always
    wins. Otherwise the reinvestment factor is rolled forward whenever the
    cumulative cash-distribution balance changes.
    """
    if not rows:
        return rows, False

    existing_history = _existing_nav_history_by_date(instrument_id)
    corrected_rows: list[dict[str, object]] = []
    recognized_cash_distribution: Decimal | None = None
    reinvested_factor: Decimal | None = None
    applied = False

    first_row_date = min(
        (
            parsed
            for row in rows
            if (parsed := _parse_nav_date(row.get("as_of_date"))) is not None
        ),
        default=None,
    )
    if first_row_date is not None:
        anchor_dates = [
            point_date
            for point_date, values in existing_history.items()
            if point_date < first_row_date
            and values.get("nav") not in {None, Decimal("0")}
            and values.get("nav_with_dividend") is not None
            and values.get("cumulative_nav") is not None
        ]
        if anchor_dates:
            anchor = existing_history[max(anchor_dates)]
            reinvested_factor = anchor["nav_with_dividend"] / anchor["nav"]
            recognized_cash_distribution = anchor["cumulative_nav"] - anchor["nav"]

    for row in sorted(rows, key=lambda item: str(item.get("as_of_date") or "")):
        corrected_row = dict(row)
        row_date = _parse_nav_date(row.get("as_of_date"))
        row_nav = _parse_nav_decimal(row.get("nav"))
        row_cumulative_nav = _parse_nav_decimal(row.get("cumulative_nav"))
        explicit_total_return_nav = _parse_nav_decimal(row.get("nav_with_dividend"))
        if row_date is None or row_nav is None:
            corrected_rows.append(corrected_row)
            continue

        cash_distribution = (
            row_cumulative_nav - row_nav
            if row_cumulative_nav is not None
            else None
        )
        if explicit_total_return_nav is not None:
            if row_nav != 0:
                reinvested_factor = explicit_total_return_nav / row_nav
            if cash_distribution is not None:
                recognized_cash_distribution = cash_distribution
            corrected_rows.append(corrected_row)
            continue
        if row_cumulative_nav is None:
            corrected_rows.append(corrected_row)
            continue

        existing_same_day = existing_history.get(row_date, {})
        existing_same_day_total = existing_same_day.get("nav_with_dividend")
        if reinvested_factor is None:
            reinvested_total_nav = existing_same_day_total or row_cumulative_nav
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
                reinvested_total_nav = row_cumulative_nav
            else:
                if cash_dividend != 0:
                    reinvested_factor = reinvested_factor * (
                        Decimal("1") + cash_dividend / row_nav
                    )
                    recognized_cash_distribution = cash_distribution
                reinvested_total_nav = row_nav * reinvested_factor

        formatted_total_nav = _format_total_return_nav_decimal(reinvested_total_nav)
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
    if full_history or nav_since_date is None:
        return False
    if not any(
        row.get("cumulative_nav") is not None and row.get("nav_with_dividend") is None
        for row in rows
    ):
        return False
    row_dates = {_parse_nav_date(row.get("as_of_date")) for row in rows}
    if nav_since_date in row_dates:
        return False
    anchor = _existing_nav_history_by_date(instrument_id).get(nav_since_date, {})
    return not all(
        anchor.get(key) is not None
        for key in ("nav", "cumulative_nav", "nav_with_dividend")
    )


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

    merged_rows = _merge_rows_by_date(filtered_rows)
    prepared_rows, _ = _apply_reinvested_total_return_correction(
        instrument_id=instrument_id,
        rows=merged_rows,
    )
    return instrument, prepared_rows


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


def _ensure_tushare_listed_security_quote_policy(
    *,
    instrument_id: str,
    instrument: dict[str, object],
) -> None:
    current_policy = instrument.get("quote_selection_policy", {})
    if not isinstance(current_policy, dict):
        current_policy = {}
    target_policy = {
        role: list(values)
        for role, values in TUSHARE_LISTED_SECURITY_QUOTE_SELECTION_POLICY.items()
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

    try:
        pro = ts.pro_api(
            settings.tushare_token,
            timeout=getattr(settings, "tushare_timeout_seconds", 30),
        )
        setattr(pro, "_DataApi__http_url", settings.tushare_api_url)
        api_method = getattr(pro, api_name)
        frame = api_method(**params, fields=fields)
    except Exception as exc:
        raise TushareRefreshError(f"Tushare SDK request failed: {exc}") from exc

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
    instrument_id: str,
    latest_date: date | None,
) -> list[dict[str, object]]:
    prepared_rows: list[dict[str, object]] = []
    for row in rows:
        point_date = _parse_nav_date(row.get("nav_date") or row.get("end_date") or row.get("ann_date"))
        if point_date is None:
            continue
        if point_date < TUSHARE_HISTORY_START_DATE:
            continue
        # Keep the current anchor date so cash-cumulative NAV can roll a
        # reinvested total-return series forward without discontinuity.
        if latest_date is not None and point_date < latest_date:
            continue
        nav = _parse_nav_decimal(row.get("unit_nav"))
        cumulative_nav = _parse_nav_decimal(row.get("accum_nav"))
        total_return_nav = _parse_nav_decimal(row.get("adj_nav"))
        if nav is None and cumulative_nav is None and total_return_nav is None:
            continue
        prepared_rows.append(
            {
                "as_of_date": point_date.isoformat(),
                "nav": nav,
                "cumulative_nav": cumulative_nav,
                "nav_with_dividend": total_return_nav,
                "currency": "CNY",
                "frequency": "daily",
            }
        )
    merged_rows = _merge_rows_by_date(prepared_rows)
    corrected_rows, _ = _apply_reinvested_total_return_correction(
        instrument_id=instrument_id,
        rows=merged_rows,
    )
    return _changed_nav_rows_since_date(
        instrument_id=instrument_id,
        rows=corrected_rows,
        nav_since_date=latest_date,
    )


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


def _tushare_adjustment_factors(
    rows: list[dict[str, object]],
) -> dict[date, Decimal]:
    factors: dict[date, Decimal] = {}
    for row in rows:
        point_date = _parse_nav_date(row.get("trade_date"))
        factor = _parse_nav_decimal(row.get("adj_factor"))
        if point_date is None or factor is None or factor <= 0:
            continue
        if point_date < TUSHARE_HISTORY_START_DATE:
            continue
        factors[point_date] = factor
    return factors


# Cash distributions also move Tushare adjustment factors, so a factor change
# by itself is not a share-split event.  We accept only conventional rational
# share ratios whose inverse mechanical move is visible in raw close while the
# adjusted series remains continuous.  Provider inference is never sufficient
# authority to change portfolio units: every candidate remains ``detected``
# until an issuer/exchange/CSD announcement confirms ratio and rounding.
TUSHARE_SHARE_SPLIT_RATIOS: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("10"), Decimal("1")),
    (Decimal("5"), Decimal("1")),
    (Decimal("4"), Decimal("1")),
    (Decimal("3"), Decimal("1")),
    (Decimal("2"), Decimal("1")),
    (Decimal("3"), Decimal("2")),
    (Decimal("5"), Decimal("4")),
    (Decimal("4"), Decimal("5")),
    (Decimal("2"), Decimal("3")),
    (Decimal("1"), Decimal("2")),
    (Decimal("1"), Decimal("3")),
    (Decimal("1"), Decimal("4")),
    (Decimal("1"), Decimal("5")),
    (Decimal("1"), Decimal("10")),
)


def _detect_tushare_share_splits(
    *,
    factors: dict[date, Decimal],
    close_by_date: dict[date, Decimal],
) -> list[dict[str, object]]:
    if len(factors) < 2 or len(close_by_date) < 2:
        return []
    factor_dates = sorted(factors)
    close_dates = sorted(close_by_date)
    candidates: list[dict[str, object]] = []
    for index, effective_date in enumerate(factor_dates[1:], start=1):
        prior_factor_date = factor_dates[index - 1]
        factor_before = factors[prior_factor_date]
        factor_after = factors[effective_date]
        if factor_before <= 0 or factor_after <= 0:
            continue
        observed_ratio = factor_after / factor_before
        nearest_new, nearest_old = min(
            TUSHARE_SHARE_SPLIT_RATIOS,
            key=lambda ratio: abs(observed_ratio - (ratio[0] / ratio[1])),
        )
        canonical_ratio = nearest_new / nearest_old
        relative_ratio_error = abs(observed_ratio / canonical_ratio - Decimal("1"))
        if relative_ratio_error > Decimal("0.005"):
            continue

        prior_close_dates = [point_date for point_date in close_dates if point_date < effective_date]
        if effective_date not in close_by_date or not prior_close_dates:
            continue
        prior_close_date = prior_close_dates[-1]
        close_before = close_by_date[prior_close_date]
        close_after = close_by_date[effective_date]
        if close_before <= 0 or close_after <= 0:
            continue
        raw_return = close_after / close_before - Decimal("1")
        adjusted_return = (close_after * observed_ratio) / close_before - Decimal("1")
        if abs(raw_return) < Decimal("0.15") or abs(adjusted_return) > Decimal("0.25"):
            continue

        candidates.append(
            {
                "effective_date": effective_date,
                "new_units": _decimal_text(nearest_new),
                "old_units": _decimal_text(nearest_old),
                "status": "detected",
                "quantity_rounding": "exact",
                "quantity_precision": 0,
                "provenance": {
                    "detection_method": "tushare_factor_price_continuity/v1",
                    "factor_before_date": prior_factor_date.isoformat(),
                    "factor_before": _decimal_text(factor_before),
                    "factor_after": _decimal_text(factor_after),
                    "observed_factor_ratio": _decimal_text(observed_ratio),
                    "canonical_ratio": f"{_decimal_text(nearest_new)}:{_decimal_text(nearest_old)}",
                    "relative_ratio_error": _decimal_text(relative_ratio_error),
                    "prior_close_date": prior_close_date.isoformat(),
                    "close_before": _decimal_text(close_before),
                    "close_after": _decimal_text(close_after),
                    "raw_return": _decimal_text(raw_return),
                    "adjusted_return": _decimal_text(adjusted_return),
                },
            }
        )
    return candidates


def _existing_price_values(
    instrument: dict[str, object],
    *,
    quote_basis: str,
) -> dict[date, Decimal]:
    values: dict[date, Decimal] = {}
    for point in list(instrument.get("market_data", [])):
        if not isinstance(point, dict):
            continue
        if str(point.get("metric_family") or "").strip() != "price":
            continue
        if str(point.get("quote_basis") or "").strip() != quote_basis:
            continue
        point_date = _parse_nav_date(point.get("as_of_date"))
        value = _parse_nav_decimal(point.get("value"))
        if point_date is not None and value is not None:
            values[point_date] = value
    return values


QFQ_FACTOR_PATTERN = re.compile(r"(?:^|:)latest_factor=([0-9]+(?:\.[0-9]+)?)$")


def _stored_qfq_latest_factor(instrument: dict[str, object]) -> Decimal | None:
    latest_point: tuple[date, Decimal] | None = None
    for point in list(instrument.get("market_data", [])):
        if not isinstance(point, dict):
            continue
        if str(point.get("quote_basis") or "").strip() != "adjusted_close":
            continue
        source_ref = str(point.get("source_ref") or "")
        match = QFQ_FACTOR_PATTERN.search(source_ref)
        point_date = _parse_nav_date(point.get("as_of_date"))
        if match is None or point_date is None:
            continue
        factor = _parse_nav_decimal(match.group(1))
        if factor is not None and (latest_point is None or point_date > latest_point[0]):
            latest_point = (point_date, factor)
    return latest_point[1] if latest_point is not None else None


def _decimal_text(value: Decimal) -> str:
    fixed = format(value, "f")
    return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed


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


def _latest_successful_email_refresh_date(instrument: dict[str, object]) -> date | None:
    refresh_status = instrument.get("refresh_status", {})
    if not isinstance(refresh_status, dict):
        return None
    raw_requested_at = str(
        refresh_status.get("last_successful_requested_at") or ""
    ).strip()
    if not raw_requested_at:
        refresh_mode = str(refresh_status.get("mode") or "").strip().lower()
        if refresh_mode not in {"", "email"}:
            return None
        if str(refresh_status.get("status") or "").strip().lower() not in {
            "imported",
            "no_match",
            "no_new_data",
        }:
            return None
        raw_requested_at = str(refresh_status.get("requested_at") or "").strip()
    if not raw_requested_at:
        return None
    try:
        requested_at = datetime.fromisoformat(raw_requested_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    # IMAP SINCE is day-granular. Keep a one-day overlap so timezone differences
    # and late mailbox delivery cannot make a successful cursor skip a message.
    return min(requested_at.date(), date.today()) - timedelta(days=1)


def _email_search_since_date(
    *,
    instrument: dict[str, object],
    nav_since_date: date | None,
    full_history: bool,
) -> date | None:
    if full_history:
        return None
    candidates = [
        candidate
        for candidate in (
            nav_since_date,
            _latest_successful_email_refresh_date(instrument),
        )
        if candidate is not None
    ]
    return max(candidates) if candidates else None


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


def _changed_nav_rows_since_date(
    *,
    instrument_id: str,
    rows: list[dict[str, object]],
    nav_since_date: date | None,
) -> list[dict[str, object]]:
    if nav_since_date is None:
        return rows
    existing_history = _existing_nav_history_by_date(instrument_id)
    changed_rows: list[dict[str, object]] = []
    for row in rows:
        row_date = _parse_nav_date(row.get("as_of_date"))
        if row_date is None or row_date < nav_since_date:
            continue
        if row_date > nav_since_date:
            changed_rows.append(row)
            continue
        existing_row = existing_history.get(row_date, {})
        incoming_nav = _parse_nav_decimal(row.get("nav"))
        incoming_cumulative_nav = _parse_nav_decimal(row.get("cumulative_nav"))
        incoming_total_nav = _parse_nav_decimal(row.get("nav_with_dividend"))
        nav_changed = incoming_nav is not None and incoming_nav != existing_row.get("nav")
        cumulative_nav_changed = (
            incoming_cumulative_nav is not None
            and incoming_cumulative_nav != existing_row.get("cumulative_nav")
        )
        total_nav_changed = (
            incoming_total_nav is not None
            and incoming_total_nav != existing_row.get("nav_with_dividend")
        )
        if nav_changed or cumulative_nav_changed or total_nav_changed:
            changed_rows.append(row)
    return changed_rows


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
    use_server_rule_search: bool = True,
) -> dict[str, object] | None:
    matched_rows: list[dict[str, object]] = []
    matched_batches: list[tuple[str, str, list[dict[str, object]]]] = []
    ordered_uids = pending_uids

    for rule in rules:
        parser_profile = str(rule.get("parser_profile") or "generic_nav_table")
        rule_uids = (
            _search_uids_for_rule(
                mailbox,
                rule=rule,
                fallback_uids=ordered_uids,
                search_criteria=search_criteria,
            )
            if use_server_rule_search
            else ordered_uids
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
                cached_parser = getattr(mailbox, "parse_attachment_rows", None)
                rows = (
                    cached_parser(
                        uid=uid,
                        attachment_name=attachment_name,
                        attachment_bytes=attachment_bytes,
                        parser_profile=parser_profile,
                    )
                    if callable(cached_parser)
                    else _parse_nav_rows_from_attachment(
                        attachment_name=attachment_name,
                        attachment_bytes=attachment_bytes,
                        parser_profile=parser_profile,
                    )
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
    if not full_history and nav_since_date is not None and not _filter_rows_since_nav_date(
        merged_rows,
        nav_since_date=nav_since_date,
    ):
        return update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=(
                "Matched email NAV rows, but none were newer than "
                f"{nav_since_date.isoformat()}."
            ),
            updated_by=updated_by,
            mode="email",
        )
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
        merged_rows = _changed_nav_rows_since_date(
            instrument_id=instrument_id,
            rows=merged_rows,
            nav_since_date=nav_since_date,
        )
    if not merged_rows:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=(
                "Matched email NAV rows, but none were newer or changed since "
                f"{nav_since_date.isoformat()}."
                if nav_since_date is not None
                else "Matched email NAV rows, but none contained importable NAV data."
            ),
            updated_by=updated_by,
            mode="email",
        )
    imported_dates = {
        row_date
        for row in merged_rows
        if (row_date := _parse_nav_date(row.get("as_of_date"))) is not None
    }
    used_source_refs: list[str] = []
    used_attachment_names: list[str] = []
    for source_ref, attachment_name, batch_rows in matched_batches:
        used_rows = (
            batch_rows
            if full_history
            else [
                row
                for row in batch_rows
                if _parse_nav_date(row.get("as_of_date")) in imported_dates
            ]
        )
        if not used_rows:
            continue
        used_source_refs.append(source_ref)
        used_attachment_names.append(attachment_name)

    if full_history:
        source_ref = "email:history"
        message = (
            f"Imported {len(merged_rows)} NAV rows from {len(used_source_refs)} "
            "email attachments."
        )
    else:
        unique_attachment_names = list(dict.fromkeys(used_attachment_names))
        source_ref = (
            f"email:{unique_attachment_names[0]}"
            if len(unique_attachment_names) == 1
            else "email:recent_window"
        )
        since_text = f" since {nav_since_date.isoformat()}" if nav_since_date else ""
        message = (
            f"Imported {len(merged_rows)} NAV rows from {len(used_source_refs)} "
            f"recent email attachments{since_text}."
        )
    if reinvested_correction_applied:
        message = f"{message} Recalculated total_return_nav using dividend reinvestment."
    return replace_nav_history(
        instrument_id=instrument_id,
        rows=merged_rows,
        source_ref=source_ref,
        point_status=_normalize_import_status(merged_rows, "complete"),
        refresh_status="imported",
        updated_by=updated_by,
        message=message,
        mode="email",
        replace_all=full_history,
    )


def import_nav_text(
    *,
    instrument_id: str,
    raw_text: str,
    source_ref: str | None,
    status: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    rows = _parse_nav_rows_from_text(raw_text)
    _, prepared_rows = _prepare_nav_rows_for_instrument(instrument_id=instrument_id, rows=rows)
    normalized_status = _normalize_import_status(prepared_rows, status)
    return replace_nav_history(
        instrument_id=instrument_id,
        rows=prepared_rows,
        source_ref=source_ref or "platform_manual_import",
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
    source_ref: str | None,
    status: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    rows = _parse_nav_rows_from_uploaded_file(file_name=file_name, file_bytes=file_bytes)
    _, prepared_rows = _prepare_nav_rows_for_instrument(instrument_id=instrument_id, rows=rows)
    normalized_status = _normalize_import_status(prepared_rows, status)
    return replace_nav_history(
        instrument_id=instrument_id,
        rows=prepared_rows,
        source_ref=source_ref or f"platform_file_import:{file_name}",
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


def refresh_market_data_with_timeout(
    *,
    instrument_id: str,
    updated_by: str | None,
    full_history: bool = False,
    source: str = "configured",
) -> dict[str, object] | None:
    try:
        with market_data_item_timeout(instrument_id):
            return refresh_market_data(
                instrument_id=instrument_id,
                updated_by=updated_by,
                full_history=full_history,
                source=source,
            )
    except MarketDataItemTimeout as exc:
        normalized_source = str(source or "configured").strip().lower()
        refresh_mode = (
            "email"
            if normalized_source == "email"
            else "api" if normalized_source == "tushare" else None
        )
        LOGGER.warning(
            "market data item timed out instrument_id=%s source=%s message=%s",
            instrument_id,
            normalized_source,
            exc,
        )
        return update_refresh_status(
            instrument_id=instrument_id,
            status="failed",
            message=str(exc),
            updated_by=updated_by,
            mode=refresh_mode,
        )


def _refresh_tushare_listed_security(
    *,
    instrument_id: str,
    instrument: dict[str, object],
    ts_code: str,
    price_api_name: str,
    factor_api_name: str,
    updated_by: str | None,
    full_history: bool,
) -> dict[str, object] | None:
    """Refresh raw close and a latest-date-normalized forward-adjusted close.

    adjusted_close[t] = close[t] * factor[t] / factor[latest]. The latest
    adjusted close therefore equals the tradable close, while historical total
    returns reflect distributions and share adjustments. If the latest factor
    changes, all available history is recomputed from canonical raw closes.
    """
    _ensure_tushare_listed_security_quote_policy(
        instrument_id=instrument_id,
        instrument=instrument,
    )
    today = date.today()
    latest_close_date = _latest_market_data_date_from_instrument(
        instrument,
        metric_family="price",
        quote_bases={"close"},
    )
    if full_history or latest_close_date is None:
        query_start = TUSHARE_HISTORY_START_DATE
    else:
        query_start = max(
            TUSHARE_HISTORY_START_DATE,
            min(latest_close_date, today) - timedelta(days=7),
        )
    params: dict[str, object] = {
        "ts_code": ts_code,
        "start_date": _format_tushare_date(query_start),
        "end_date": _format_tushare_date(today),
    }
    close_rows = _tushare_price_rows(
        _call_tushare_api(
            api_name=price_api_name,
            params=params,
            fields="ts_code,trade_date,close",
        ),
        latest_date=None,
    )
    factor_rows = _call_tushare_api(
        api_name=factor_api_name,
        params=params,
        fields="ts_code,trade_date,adj_factor",
    )
    factors = _tushare_adjustment_factors(factor_rows)
    previous_latest_factor = _stored_qfq_latest_factor(instrument)
    current_latest_factor = factors[max(factors)] if factors else previous_latest_factor
    factor_changed = (
        current_latest_factor is not None
        and (
            previous_latest_factor is None
            or current_latest_factor != previous_latest_factor
        )
    )
    if current_latest_factor is not None and (full_history or factor_changed) and query_start > TUSHARE_HISTORY_START_DATE:
        full_factor_rows = _call_tushare_api(
            api_name=factor_api_name,
            params={
                "ts_code": ts_code,
                "start_date": _format_tushare_date(TUSHARE_HISTORY_START_DATE),
                "end_date": _format_tushare_date(today),
            },
            fields="ts_code,trade_date,adj_factor",
        )
        factors = _tushare_adjustment_factors(full_factor_rows)
        if factors:
            current_latest_factor = factors[max(factors)]

    close_by_date = _existing_price_values(instrument, quote_basis="close")
    fetched_close_by_date = {
        row["as_of_date"]: Decimal(str(row["value"]))
        for row in close_rows
        if isinstance(row.get("as_of_date"), date)
    }
    close_by_date.update(fetched_close_by_date)
    points: list[dict[str, object]] = [
        {
            "metric_family": "price",
            "quote_basis": "close",
            "as_of_date": point_date,
            "value": value,
            "currency": "CNY",
            "source_ref": f"tushare:{price_api_name}",
            "status": "complete",
        }
        for point_date, value in fetched_close_by_date.items()
    ]
    if current_latest_factor is not None:
        adjusted_dates = (
            sorted(close_by_date)
            if full_history or factor_changed
            else sorted(fetched_close_by_date)
        )
        factor_text = _decimal_text(current_latest_factor)
        for point_date in adjusted_dates:
            factor = factors.get(point_date)
            close_value = close_by_date.get(point_date)
            if factor is None or close_value is None:
                continue
            adjusted_close = close_value * factor / current_latest_factor
            points.append(
                {
                    "metric_family": "price",
                    "quote_basis": "adjusted_close",
                    "as_of_date": point_date,
                    "value": _decimal_text(adjusted_close),
                    "currency": "CNY",
                    "source_ref": (
                        f"tushare:{factor_api_name}:qfq:latest_factor={factor_text}"
                    ),
                    "status": "complete",
                }
            )

    changed_count = upsert_market_data_points(
        instrument_id=instrument_id,
        rows=points,
    )
    if changed_count is None:
        return None
    detected_actions = _detect_tushare_share_splits(
        factors=factors,
        close_by_date=close_by_date,
    )
    persisted_actions: list[dict[str, object]] = []
    for action in detected_actions:
        persisted_action = upsert_corporate_action_event(
            instrument_id=instrument_id,
            action_type="share_split",
            effective_date=action["effective_date"],
            new_units=action["new_units"],
            old_units=action["old_units"],
            source=f"tushare:{factor_api_name}",
            status=str(action["status"]),
            quantity_rounding=str(action["quantity_rounding"]),
            quantity_precision=int(action["quantity_precision"]),
            external_event_id=(
                f"{ts_code}:{action['effective_date'].isoformat()}:share_split"
            ),
            provenance=dict(action["provenance"]),
        )
        if isinstance(persisted_action, dict):
            persisted_actions.append(persisted_action)
    if changed_count == 0:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=f"Tushare returned no changed listed-security rows for {ts_code}.",
            updated_by=updated_by,
            mode="api",
        )
    adjusted_count = sum(
        1 for point in points if point.get("quote_basis") == "adjusted_close"
    )
    confirmed_action_count = sum(
        1 for action in persisted_actions if action.get("status") == "confirmed"
    )
    detected_action_count = sum(
        1 for action in persisted_actions if action.get("status") == "detected"
    )
    return update_refresh_status(
        instrument_id=instrument_id,
        status="refreshed",
        message=(
            f"Updated {changed_count} market-data points for {ts_code}: "
            f"raw close for valuation/trading and {adjusted_count} qfq adjusted closes "
            "for charts and total return; "
            f"{confirmed_action_count} confirmed and {detected_action_count} review-required "
            "share-adjustment event(s)."
        ),
        updated_by=updated_by,
        mode="api",
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
            nav_rows = _tushare_nav_rows(
                rows,
                instrument_id=instrument_id,
                latest_date=latest_date,
            )
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
                source_ref="tushare:fund_nav",
                point_status="complete",
                refresh_status="imported",
                updated_by=updated_by,
                message=f"Imported {len(nav_rows)} NAV rows from Tushare fund_nav for {ts_code}.",
                mode="api",
                replace_all=full_history,
            )

        if instrument_type in {"fund", "etf"} and suffix in TUSHARE_PRICE_SUFFIXES:
            return _refresh_tushare_listed_security(
                instrument_id=instrument_id,
                instrument=instrument,
                ts_code=ts_code,
                price_api_name="fund_daily",
                factor_api_name="fund_adj",
                updated_by=updated_by,
                full_history=full_history,
            )

        if instrument_type == "equity" and suffix in TUSHARE_PRICE_SUFFIXES:
            return _refresh_tushare_listed_security(
                instrument_id=instrument_id,
                instrument=instrument,
                ts_code=ts_code,
                price_api_name="daily",
                factor_api_name="adj_factor",
                updated_by=updated_by,
                full_history=full_history,
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

    changed_count = upsert_market_data_points(
        instrument_id=instrument_id,
        rows=[
            {
                "metric_family": "price",
                "quote_basis": "close",
                "as_of_date": row["as_of_date"],
                "value": row["value"],
                "currency": "CNY",
                "source_ref": f"tushare:{api_name}",
                "status": "complete",
            }
            for row in rows
        ],
    )
    if changed_count == 0:
        return update_refresh_status(
            instrument_id=instrument_id,
            status="no_new_data",
            message=f"Tushare {api_name} returned no changed close rows for {ts_code}.",
            updated_by=updated_by,
            mode="api",
        )
    return update_refresh_status(
        instrument_id=instrument_id,
        status="refreshed",
        message=f"Imported {changed_count} close rows from Tushare {api_name} for {ts_code}.",
        updated_by=updated_by,
        mode="api",
    )


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


class _EmailMailboxConnectionError(RuntimeError):
    pass


class _EmailMailboxFolderError(RuntimeError):
    pass


def _imap_status_ok(status: object) -> bool:
    if isinstance(status, bytes):
        return status.upper() == b"OK"
    return str(status or "").upper() == "OK"


class _EmailMailboxSession:
    """Own one IMAP login and safely reuse it across an email refresh batch."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self._mailbox: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
        self._selected_folder: str | None = None
        self._search_cache: dict[tuple[str, tuple[object, ...]], tuple[object, object]] = {}
        self._message_cache: OrderedDict[tuple[str, int, str], bytes | None] = OrderedDict()
        self._message_cache_bytes = 0
        self._message_cache_limit_bytes = 64 * 1024 * 1024
        self._attachment_rows_cache: dict[
            tuple[str, int, str, str], list[dict[str, object]]
        ] = {}

    @staticmethod
    def _discard_mailbox(mailbox: object) -> None:
        shutdown = getattr(mailbox, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
                return
            except Exception:
                pass
        logout = getattr(mailbox, "logout", None)
        if callable(logout):
            try:
                logout()
            except Exception:
                pass

    def _connect(self) -> imaplib.IMAP4 | imaplib.IMAP4_SSL:
        mailbox_cls = imaplib.IMAP4_SSL if self.settings.email_imap_use_ssl else imaplib.IMAP4
        mailbox = mailbox_cls(
            self.settings.email_imap_host,
            self.settings.email_imap_port,
            timeout=self.settings.email_imap_timeout_seconds,
        )
        try:
            login_status, _ = mailbox.login(
                self.settings.email_imap_username,
                self.settings.email_imap_password,
            )
            if not _imap_status_ok(login_status):
                raise _EmailMailboxConnectionError("IMAP login failed.")
        except Exception:
            self._discard_mailbox(mailbox)
            raise
        self._mailbox = mailbox
        self._selected_folder = None
        return mailbox

    def open_folder(self, preferred_folder: str) -> "_EmailMailboxSession":
        mailbox = self._mailbox or self._connect()
        folders = list(
            dict.fromkeys(
                folder
                for folder in (preferred_folder, self.settings.email_imap_folder)
                if folder
            )
        )
        for folder in folders:
            if folder == self._selected_folder:
                return self
            select_status, _ = mailbox.select(
                folder,
                readonly=not self.settings.email_imap_mark_seen,
            )
            if _imap_status_ok(select_status):
                self._selected_folder = folder
                return self
            # Do not assume the previous mailbox remains selected after a failed SELECT.
            self._selected_folder = None
        raise _EmailMailboxFolderError("unable to open the configured mailbox folder.")

    def uid(self, command: str, *args: object):
        mailbox = self._mailbox
        if mailbox is None:
            raise _EmailMailboxConnectionError("IMAP session is not connected.")
        normalized_command = command.strip().lower()
        if normalized_command != "search" or self._selected_folder is None:
            return mailbox.uid(command, *args)
        cache_key = (self._selected_folder, tuple(args))
        cached = self._search_cache.get(cache_key)
        if cached is not None:
            return cached
        result = mailbox.uid(command, *args)
        if _imap_status_ok(result[0]):
            self._search_cache[cache_key] = result
        return result

    def fetch_message_bytes(self, uid: int, request: str) -> bytes | None:
        folder = self._selected_folder
        mailbox = self._mailbox
        if folder is None or mailbox is None:
            raise _EmailMailboxConnectionError("IMAP folder is not selected.")
        cache_key = (folder, uid, request)
        if cache_key in self._message_cache:
            cached = self._message_cache.pop(cache_key)
            self._message_cache[cache_key] = cached
            return cached
        fetch_status, fetch_data = mailbox.uid("fetch", str(uid), request)
        payload = None
        if _imap_status_ok(fetch_status):
            payload = next(
                (
                    bytes(item[1])
                    for item in fetch_data
                    if isinstance(item, tuple)
                    and len(item) >= 2
                    and isinstance(item[1], (bytes, bytearray))
                ),
                None,
            )
        payload_size = len(payload) if payload is not None else 0
        while self._message_cache and (
            self._message_cache_bytes + payload_size > self._message_cache_limit_bytes
        ):
            _, evicted = self._message_cache.popitem(last=False)
            self._message_cache_bytes -= len(evicted) if evicted is not None else 0
        if payload_size <= self._message_cache_limit_bytes:
            self._message_cache[cache_key] = payload
            self._message_cache_bytes += payload_size
        return payload

    def parse_attachment_rows(
        self,
        *,
        uid: int,
        attachment_name: str,
        attachment_bytes: bytes,
        parser_profile: str,
    ) -> list[dict[str, object]]:
        folder = self._selected_folder or ""
        cache_key = (folder, uid, attachment_name, parser_profile)
        cached = self._attachment_rows_cache.get(cache_key)
        if cached is not None:
            return cached
        rows = _parse_nav_rows_from_attachment(
            attachment_name=attachment_name,
            attachment_bytes=attachment_bytes,
            parser_profile=parser_profile,
        )
        self._attachment_rows_cache[cache_key] = rows
        return rows

    def invalidate(self) -> None:
        mailbox = self._mailbox
        self._mailbox = None
        self._selected_folder = None
        self._search_cache.clear()
        if mailbox is not None:
            self._discard_mailbox(mailbox)

    def close(self) -> None:
        mailbox = self._mailbox
        selected_folder = self._selected_folder
        self._mailbox = None
        self._selected_folder = None
        if mailbox is None:
            return
        if selected_folder is not None:
            try:
                mailbox.close()
            except Exception:
                pass
        try:
            mailbox.logout()
        except Exception:
            pass


def _tushare_refresh_process_worker(
    send_connection: Any,
    instrument_id: str,
    updated_by: str | None,
    full_history: bool,
) -> None:
    """Run one refresh in a killable process, isolating a stuck SDK request."""
    try:
        record = refresh_market_data(
            instrument_id=instrument_id,
            updated_by=updated_by,
            full_history=full_history,
            source="configured",
        )
        send_connection.send({"ok": True, "record": record})
    except BaseException as exc:  # pragma: no cover - defensive process boundary
        send_connection.send(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    finally:
        send_connection.close()


def _stop_refresh_process(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        process.join(timeout=0.1)
        return
    process.terminate()
    process.join(timeout=2)
    if process.is_alive():
        process.kill()
        process.join(timeout=2)


def _run_tushare_refresh_process_batch(
    *,
    targets: list[dict[str, object]],
    updated_by: str | None,
    full_history: bool,
    settings: Any,
) -> list[dict[str, object] | None]:
    """Refresh Tushare targets concurrently with hard item and batch deadlines.

    Threads cannot be cancelled safely while blocked inside requests. A fresh,
    daemon child process per instrument gives the scheduler a real termination
    boundary and also releases database/network resources if the provider hangs.
    """
    if not targets:
        return []
    context = multiprocessing.get_context("spawn")
    max_workers = min(max(1, int(getattr(settings, "tushare_batch_max_workers", 4))), len(targets))
    configured_item_timeout = int(
        getattr(settings, "market_data_batch_item_timeout_seconds", 300) or 0
    )
    request_timeout = max(1, int(getattr(settings, "tushare_timeout_seconds", 30) or 30))
    item_timeout = (
        configured_item_timeout
        if configured_item_timeout > 0
        else max(60, request_timeout * 4)
    )
    configured_batch_timeout = int(
        getattr(settings, "tushare_batch_timeout_seconds", 3600) or 0
    )
    batch_timeout = (
        configured_batch_timeout
        if configured_batch_timeout > 0
        else item_timeout * ((len(targets) + max_workers - 1) // max_workers) + 60
    )
    batch_started = time.monotonic()
    ordered_results: list[dict[str, object] | None] = [None] * len(targets)
    pending_indexes = list(range(len(targets)))
    running: dict[int, tuple[multiprocessing.Process, Any, float]] = {}

    def failed_record(index: int, message: str) -> dict[str, object] | None:
        instrument_id = str(targets[index].get("instrument_id") or "")
        return update_refresh_status(
            instrument_id=instrument_id,
            status="failed",
            message=message,
            updated_by=updated_by,
            mode="api",
        )

    def start_one(index: int) -> None:
        instrument_id = str(targets[index].get("instrument_id") or "")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_tushare_refresh_process_worker,
            args=(send_connection, instrument_id, updated_by, full_history),
            name=f"tushare-refresh-{instrument_id}",
            daemon=True,
        )
        try:
            process.start()
        except Exception as exc:
            receive_connection.close()
            send_connection.close()
            ordered_results[index] = failed_record(
                index,
                f"Unable to start isolated Tushare refresh: {exc}",
            )
            return
        send_connection.close()
        running[index] = (process, receive_connection, time.monotonic())

    while pending_indexes or running:
        now = time.monotonic()
        batch_expired = now - batch_started >= batch_timeout
        if not batch_expired:
            while pending_indexes and len(running) < max_workers:
                start_one(pending_indexes.pop(0))

        completed_indexes: list[int] = []
        for index, (process, receive_connection, started_at) in list(running.items()):
            payload: dict[str, object] | None = None
            try:
                if receive_connection.poll():
                    candidate = receive_connection.recv()
                    payload = candidate if isinstance(candidate, dict) else None
                elif not process.is_alive():
                    payload = {
                        "ok": False,
                        "error": f"worker exited with code {process.exitcode} without a result",
                    }
            except (EOFError, OSError) as exc:
                payload = {"ok": False, "error": f"worker channel failed: {exc}"}

            timed_out = now - started_at >= item_timeout
            if payload is None and not timed_out and not batch_expired:
                continue

            if payload is not None and payload.get("ok") is True:
                record = payload.get("record")
                ordered_results[index] = record if isinstance(record, dict) else None
            else:
                instrument_id = str(targets[index].get("instrument_id") or "")
                if batch_expired:
                    message = (
                        f"Tushare batch deadline exceeded after {batch_timeout} seconds "
                        f"while refreshing {instrument_id}."
                    )
                elif timed_out:
                    message = (
                        f"Tushare refresh timed out after {item_timeout} seconds for "
                        f"{instrument_id}; the isolated worker was terminated."
                    )
                else:
                    message = f"Tushare refresh worker failed for {instrument_id}: {payload.get('error') if payload else 'unknown error'}"
                ordered_results[index] = failed_record(index, message)
            _stop_refresh_process(process)
            receive_connection.close()
            completed_indexes.append(index)

        for index in completed_indexes:
            running.pop(index, None)

        if batch_expired:
            for index in pending_indexes:
                instrument_id = str(targets[index].get("instrument_id") or "")
                ordered_results[index] = failed_record(
                    index,
                    f"Tushare batch deadline exceeded after {batch_timeout} seconds before {instrument_id} could start.",
                )
            pending_indexes.clear()
        if pending_indexes or running:
            time.sleep(0.05)

    return ordered_results


def refresh_market_data_batch(
    *,
    source: str,
    updated_by: str | None,
    full_history: bool = False,
    include_inactive: bool = False,
) -> dict[str, object]:
    settings = get_settings()
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
    if normalized_source == "tushare" and targets:
        max_workers = min(getattr(settings, "tushare_batch_max_workers", 4), len(targets))
        LOGGER.info(
            "tushare batch executing in isolated processes target_count=%s max_workers=%s item_timeout=%s batch_timeout=%s",
            len(targets),
            max_workers,
            getattr(settings, "market_data_batch_item_timeout_seconds", 300),
            getattr(settings, "tushare_batch_timeout_seconds", 3600),
        )
        ordered_results = _run_tushare_refresh_process_batch(
            targets=targets,
            updated_by=updated_by,
            full_history=full_history,
            settings=settings,
        )
        results = []
        for refreshed in ordered_results:
            if refreshed is None:
                continue
            source_settings = dict(refreshed.get("source_settings", {}))
            refresh_status = dict(refreshed.get("refresh_status", {}))
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
            "refreshed_count": sum(
                1 for item in results if item["status"] in {"imported", "refreshed"}
            ),
            "skipped_count": len(instruments) - len(targets),
            "results": results,
        }
    results: list[dict[str, object]] = []
    has_email_targets = any(
        str(dict(item.get("source_settings", {})).get("source_mode") or "").strip().lower()
        == "email"
        for item in targets
    )
    email_session = _EmailMailboxSession(settings) if has_email_targets else None
    email_target_details: dict[str, dict[str, object]] = {}
    email_batch_uids_by_folder: dict[str, list[int]] = {}
    if email_session is not None:
        earliest_cursor_by_folder: dict[str, date | None] = {}
        for target in targets:
            target_settings = dict(target.get("source_settings", {}))
            if str(target_settings.get("source_mode") or "").strip().lower() != "email":
                continue
            instrument_id = str(target.get("instrument_id") or "")
            detail = get_instrument(instrument_id)
            if detail is None:
                continue
            email_target_details[instrument_id] = detail
            detail_settings = dict(detail.get("source_settings", {}))
            folder = str(
                detail_settings.get("source_location") or settings.email_imap_folder
            ).strip() or settings.email_imap_folder
            cursor = _email_search_since_date(
                instrument=detail,
                nav_since_date=(
                    None if full_history else _latest_nav_date_from_instrument(detail)
                ),
                full_history=full_history,
            )
            if folder not in earliest_cursor_by_folder:
                earliest_cursor_by_folder[folder] = cursor
            elif cursor is None:
                earliest_cursor_by_folder[folder] = None
            elif earliest_cursor_by_folder[folder] is not None:
                earliest_cursor_by_folder[folder] = min(
                    cursor,
                    earliest_cursor_by_folder[folder],
                )
        for folder, earliest_cursor in earliest_cursor_by_folder.items():
            try:
                mailbox = email_session.open_folder(folder)
                criteria = (
                    ("ALL",)
                    if earliest_cursor is None
                    else ("SINCE", _format_imap_since_date(earliest_cursor))
                )
                search_status, search_data = mailbox.uid("search", None, *criteria)
                if not _imap_status_ok(search_status):
                    continue
                raw_uid_list = search_data[0] if search_data and search_data[0] else b""
                available_uids = [int(item) for item in raw_uid_list.split() if item]
                pending_uids = (
                    available_uids
                    if earliest_cursor is not None
                    else available_uids[-settings.email_imap_max_messages :]
                )
                email_batch_uids_by_folder[folder] = pending_uids
                if email_session._selected_folder:
                    email_batch_uids_by_folder[email_session._selected_folder] = pending_uids
                LOGGER.info(
                    "email batch snapshot folder=%s cursor=%s uid_count=%s",
                    folder,
                    earliest_cursor.isoformat() if earliest_cursor else "all-limited",
                    len(pending_uids),
                )
            except Exception:
                LOGGER.exception("email batch snapshot failed folder=%s", folder)
                email_session.invalidate()
    try:
        for index, instrument in enumerate(targets, start=1):
            instrument_id = str(instrument.get("instrument_id") or "")
            target_source_mode = str(
                dict(instrument.get("source_settings", {})).get("source_mode") or "manual"
            ).strip().lower()
            LOGGER.info(
                "market data batch item started source=%s index=%s/%s instrument_id=%s",
                normalized_source,
                index,
                len(targets),
                instrument_id,
            )
            try:
                with market_data_item_timeout(instrument_id):
                    if target_source_mode == "email" and email_session is not None:
                        current_instrument = email_target_details.get(instrument_id) or get_instrument(instrument_id)
                        if current_instrument is None:
                            refreshed = None
                        else:
                            current_source_settings = dict(
                                current_instrument.get("source_settings", {})
                            )
                            if (
                                str(current_source_settings.get("source_mode") or "manual")
                                .strip()
                                .lower()
                                == "email"
                            ):
                                refreshed = _refresh_from_email(
                                    instrument_id=instrument_id,
                                    instrument=current_instrument,
                                    source_settings=current_source_settings,
                                    updated_by=updated_by,
                                    full_history=full_history,
                                    mailbox_session=email_session,
                                    batch_pending_uids=email_batch_uids_by_folder.get(
                                        str(
                                            current_source_settings.get("source_location")
                                            or settings.email_imap_folder
                                        ).strip()
                                        or settings.email_imap_folder
                                    ),
                                )
                            else:
                                refreshed = refresh_market_data(
                                    instrument_id=instrument_id,
                                    updated_by=updated_by,
                                    full_history=full_history,
                                    source="configured",
                                )
                    else:
                        refreshed = refresh_market_data(
                            instrument_id=instrument_id,
                            updated_by=updated_by,
                            full_history=full_history,
                            source="configured",
                        )
            except MarketDataItemTimeout as exc:
                if email_session is not None:
                    email_session.invalidate()
                LOGGER.warning(
                    "market data batch item timed out source=%s index=%s/%s instrument_id=%s timeout_seconds=%s",
                    normalized_source,
                    index,
                    len(targets),
                    instrument_id,
                    settings.market_data_batch_item_timeout_seconds,
                )
                refreshed = update_refresh_status(
                    instrument_id=instrument_id,
                    status="failed",
                    message=str(exc),
                    updated_by=updated_by,
                    mode=target_source_mode,
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
    finally:
        if email_session is not None:
            email_session.close()
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
    mailbox_session: _EmailMailboxSession | None = None,
    batch_pending_uids: list[int] | None = None,
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

    owns_session = mailbox_session is None
    session = mailbox_session or _EmailMailboxSession(settings)
    try:
        for attempt in range(2):
            try:
                mailbox = session.open_folder(preferred_folder)
                nav_since_date = None if full_history else _latest_nav_date_from_instrument(instrument)
                search_since_date = _email_search_since_date(
                    instrument=instrument,
                    nav_since_date=nav_since_date,
                    full_history=full_history,
                )
                search_criteria = (
                    ("ALL",)
                    if search_since_date is None
                    else ("SINCE", _format_imap_since_date(search_since_date))
                )
                if batch_pending_uids is None:
                    search_status, search_data = mailbox.uid("search", None, *search_criteria)
                    if not _imap_status_ok(search_status):
                        raise _EmailMailboxConnectionError("unable to list mailbox messages.")
                    raw_uid_list = search_data[0] if search_data and search_data[0] else b""
                    available_uids = [int(item) for item in raw_uid_list.split() if item]
                    pending_uids = (
                        available_uids
                        if search_since_date is not None
                        else available_uids[-settings.email_imap_max_messages :]
                    )
                else:
                    pending_uids = batch_pending_uids

                record = _import_rows_from_email_rules(
                    instrument_id=instrument_id,
                    rules=email_rules,
                    mailbox=mailbox,
                    pending_uids=pending_uids,
                    updated_by=updated_by,
                    full_history=full_history,
                    nav_since_date=nav_since_date,
                    search_criteria=search_criteria,
                    use_server_rule_search=batch_pending_uids is None,
                )
                if record is not None:
                    return record

                since_text = (
                    f" received since {search_since_date.isoformat()}"
                    if search_since_date
                    else ""
                )
                return update_refresh_status(
                    instrument_id=instrument_id,
                    status="no_match",
                    message=(
                        "No email attachment matched the explicit product rules"
                        f"{since_text}."
                    ),
                    updated_by=updated_by,
                    mode="email",
                )
            except MarketDataItemTimeout:
                session.invalidate()
                raise
            except _EmailMailboxFolderError as exc:
                return update_refresh_status(
                    instrument_id=instrument_id,
                    status="blocked",
                    message=f"Email refresh failed: {exc}",
                    updated_by=updated_by,
                    mode="email",
                )
            except (
                _EmailMailboxConnectionError,
                imaplib.IMAP4.abort,
                imaplib.IMAP4.error,
                OSError,
                EOFError,
            ) as exc:
                session.invalidate()
                if attempt == 0:
                    LOGGER.warning(
                        "email refresh IMAP session failed; reconnecting instrument_id=%s error=%s",
                        instrument_id,
                        exc,
                    )
                    continue
                return update_refresh_status(
                    instrument_id=instrument_id,
                    status="failed",
                    message=f"Email refresh failed: {exc}",
                    updated_by=updated_by,
                    mode="email",
                )
            except Exception as exc:
                session.invalidate()
                return update_refresh_status(
                    instrument_id=instrument_id,
                    status="failed",
                    message=f"Email refresh failed: {exc}",
                    updated_by=updated_by,
                    mode="email",
                )
    finally:
        if owns_session:
            session.close()

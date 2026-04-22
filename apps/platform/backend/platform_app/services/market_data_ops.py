from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from io import BytesIO
import imaplib
import re
from typing import Any

from platform_app.core.settings import get_settings
from platform_app.services.instrument_store import (
    get_instrument,
    replace_nav_history,
    update_refresh_status,
)

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - optional dependency
    load_workbook = None

try:
    import xlrd
except ImportError:  # pragma: no cover - optional dependency
    xlrd = None


NAV_IMPORT_HEADER_MAP = {
    "date": "as_of_date",
    "日期": "as_of_date",
    "净值日期": "as_of_date",
    "业务日期": "as_of_date",
    "估值基准日": "as_of_date",
    "asofdate": "as_of_date",
    "as_of_date": "as_of_date",
    "trade_date": "as_of_date",
    "navdate": "as_of_date",
    "nav": "nav",
    "净值": "nav",
    "单位净值": "nav",
    "单位净值元": "nav",
    "资产份额净值元": "nav",
    "实际净值": "nav",
    "navwithdividend": "nav_with_dividend",
    "nav_with_dividend": "nav_with_dividend",
    "累计净值": "nav_with_dividend",
    "累计净值元": "nav_with_dividend",
    "累计单位净值": "nav_with_dividend",
    "累计单位净值元": "nav_with_dividend",
    "资产份额累计净值元": "nav_with_dividend",
    "实际累计净值": "nav_with_dividend",
    "currency": "currency",
    "ccy": "currency",
    "币种": "currency",
    "frequency": "frequency",
    "freq": "frequency",
    "频率": "frequency",
    "productcode": "asset_code",
    "产品代码": "asset_code",
    "assetcode": "asset_code",
    "资产代码": "asset_code",
    "fundcode": "asset_code",
    "基金代码": "asset_code",
    "productname": "asset_name",
    "产品名称": "asset_name",
    "assetname": "asset_name",
    "资产名称": "asset_name",
    "fundname": "asset_name",
    "基金名称": "asset_name",
}

LABEL_SNAPSHOT_FIELD_ALIASES = {
    "as_of_date": ("日期", "净值日期"),
    "nav": ("单位净值",),
    "nav_with_dividend": ("累计单位净值",),
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
            elif column in {"asset_code", "asset_name"} and cell:
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


def _message_matches_instrument(
    *,
    instrument: dict[str, object],
    subject: str,
    body_text: str,
    attachment_names: list[str],
) -> bool:
    corpus = " ".join([subject, body_text, *attachment_names]).lower()
    normalized_corpus = _normalize_text_token(corpus)
    raw_candidates: list[str] = []
    normalized_candidates: list[str] = []
    for value in [
        str(instrument.get("asset_id") or ""),
        str(instrument.get("asset_name") or ""),
        *[
            str(item.get("identifier_value") or "")
            for item in list(instrument.get("identifiers", []))
        ],
    ]:
        trimmed = value.strip()
        if not trimmed:
            continue
        lowered = trimmed.lower()
        normalized = _normalize_text_token(trimmed)
        if len(lowered) >= 3:
            raw_candidates.append(lowered)
        if len(normalized) >= 3:
            normalized_candidates.append(normalized)
    return any(token in corpus for token in raw_candidates) or any(
        token in normalized_corpus for token in normalized_candidates
    )


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
    row_code = str(row.get("asset_code") or "").strip().upper()
    row_name = _normalize_text_token(str(row.get("asset_name") or ""))

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


def _search_uids_for_rule(
    mailbox,
    *,
    rule: dict[str, object],
    fallback_uids: list[int],
) -> list[int]:
    sender_equals = list(rule.get("sender_equals", []))
    if not sender_equals:
        return fallback_uids

    matched: set[int] = set()
    for sender in sender_equals:
        search_status, search_data = mailbox.uid("search", None, "FROM", sender)
        if search_status != "OK":
            return fallback_uids
        raw_uid_list = search_data[0] if search_data and search_data[0] else b""
        matched.update(int(item) for item in raw_uid_list.split() if item)
    if not matched:
        return []
    return [uid for uid in sorted(matched) if uid in set(fallback_uids)]


def _import_rows_from_email_rules(
    *,
    asset_id: str,
    rules: list[dict[str, object]],
    mailbox,
    pending_uids: list[int],
    updated_by: str | None,
    full_history: bool,
) -> dict[str, object] | None:
    matched_rows: list[dict[str, object]] = []
    matched_providers: list[str] = []
    ordered_uids = pending_uids if full_history else list(reversed(pending_uids))

    for rule in rules:
        parser_profile = str(rule.get("parser_profile") or "generic_nav_table")
        rule_uids = _search_uids_for_rule(
            mailbox,
            rule=rule,
            fallback_uids=ordered_uids,
        )
        candidate_uids = rule_uids if full_history else list(reversed(rule_uids))
        for uid in candidate_uids:
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
                if full_history:
                    matched_rows.extend(rows)
                    matched_providers.append(f"{uid}:{attachment_name}")
                    break
                return replace_nav_history(
                    asset_id=asset_id,
                    rows=rows,
                    provider=f"email:{attachment_name}",
                    point_status=_normalize_import_status(rows, "complete"),
                    refresh_status="imported",
                    updated_by=updated_by,
                    message=f"Imported {len(rows)} NAV rows from email attachment {attachment_name}.",
                    mode="email",
                )

    if not full_history:
        return None

    merged_rows = _merge_rows_by_date(matched_rows)
    if not merged_rows:
        return None
    return replace_nav_history(
        asset_id=asset_id,
        rows=merged_rows,
        provider="email:history",
        point_status=_normalize_import_status(merged_rows, "complete"),
        refresh_status="imported",
        updated_by=updated_by,
        message=(
            f"Imported {len(merged_rows)} NAV rows from {len(matched_providers)} "
            "email attachments."
        ),
        mode="email",
    )


def import_nav_text(
    *,
    asset_id: str,
    raw_text: str,
    provider: str | None,
    status: str,
    updated_by: str | None,
) -> dict[str, object] | None:
    rows = _parse_nav_rows_from_text(raw_text)
    if not rows:
        raise ValueError(
            "No NAV rows detected. Paste CSV/TSV text with date and nav/nav_with_dividend columns."
        )
    normalized_status = _normalize_import_status(rows, status)
    return replace_nav_history(
        asset_id=asset_id,
        rows=rows,
        provider=provider or "platform_manual_import",
        point_status=normalized_status,
        refresh_status="imported",
        updated_by=updated_by,
        message=f"Imported {len(rows)} NAV rows into shared market data.",
        mode="manual",
    )


def refresh_market_data(
    *,
    asset_id: str,
    updated_by: str | None,
    full_history: bool = False,
) -> dict[str, object] | None:
    instrument = get_instrument(asset_id)
    if instrument is None:
        return None

    source_settings = dict(instrument.get("source_settings", {}))
    source_mode = str(source_settings.get("source_mode") or "manual")
    if source_mode == "email":
        return _refresh_from_email(
            asset_id=asset_id,
            instrument=instrument,
            source_settings=source_settings,
            updated_by=updated_by,
            full_history=full_history,
        )
    if source_mode == "api":
        profile = str(source_settings.get("source_api_profile") or "").strip()
        return update_refresh_status(
            asset_id=asset_id,
            status="blocked",
            message=(
                f"API refresh profile {profile or 'unconfigured profile'} is not wired in shared data ops yet."
            ),
            updated_by=updated_by,
            mode="api",
        )

    location = str(source_settings.get("source_location") or "Shared data ops").strip()
    return update_refresh_status(
        asset_id=asset_id,
        status="awaiting_manual_import",
        message=f"Manual source active in shared data ops. Import canonical NAV from {location}.",
        updated_by=updated_by,
        mode="manual",
    )


def _refresh_from_email(
    *,
    asset_id: str,
    instrument: dict[str, object],
    source_settings: dict[str, object],
    updated_by: str | None,
    full_history: bool,
) -> dict[str, object] | None:
    settings = get_settings()
    if not settings.email_sync_enabled:
        return update_refresh_status(
            asset_id=asset_id,
            status="blocked",
            message="Email refresh is disabled. Set YUNGU_PLATFORM_EMAIL_SYNC_ENABLED=true first.",
            updated_by=updated_by,
            mode="email",
        )
    if not settings.email_sync_ready:
        return update_refresh_status(
            asset_id=asset_id,
            status="blocked",
            message="Email refresh is not configured. Set IMAP host, username, and password.",
            updated_by=updated_by,
            mode="email",
        )

    mailbox: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None
    expected_sender = str(source_settings.get("source_email", "")).strip().lower()
    preferred_folder = str(source_settings.get("source_location", "")).strip()
    email_rules = _normalized_email_rules(source_settings)
    try:
        mailbox_cls = imaplib.IMAP4_SSL if settings.email_imap_use_ssl else imaplib.IMAP4
        mailbox = mailbox_cls(settings.email_imap_host, settings.email_imap_port, timeout=300)
        login_status, _ = mailbox.login(settings.email_imap_username, settings.email_imap_password)
        if login_status != "OK":
            return update_refresh_status(
                asset_id=asset_id,
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
                asset_id=asset_id,
                status="blocked",
                message="Email refresh failed: unable to open the configured mailbox folder.",
                updated_by=updated_by,
                mode="email",
            )

        search_status, search_data = mailbox.uid("search", None, "ALL")
        if search_status != "OK":
            return update_refresh_status(
                asset_id=asset_id,
                status="failed",
                message="Email refresh failed: unable to list mailbox messages.",
                updated_by=updated_by,
                mode="email",
            )
        raw_uid_list = search_data[0] if search_data and search_data[0] else b""
        available_uids = [int(item) for item in raw_uid_list.split() if item]
        pending_uids = available_uids if full_history else available_uids[-settings.email_imap_max_messages :]

        if email_rules:
            record = _import_rows_from_email_rules(
                asset_id=asset_id,
                rules=email_rules,
                mailbox=mailbox,
                pending_uids=pending_uids,
                updated_by=updated_by,
                full_history=full_history,
            )
            if record is not None:
                return record

        for uid in reversed(pending_uids):
            fetch_status, fetch_data = mailbox.uid("fetch", str(uid), "(RFC822)")
            if fetch_status != "OK":
                continue
            raw_bytes = next(
                (
                    bytes(item[1])
                    for item in fetch_data
                    if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray))
                ),
                None,
            )
            if raw_bytes is None:
                continue
            message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
            subject = str(message.get("subject") or "")
            sender = str(message.get("from") or "")
            sender_email = parseaddr(sender)[1].strip().lower()
            if expected_sender and sender_email != expected_sender:
                continue

            body_text = _extract_email_body_text(message)
            attachment_candidates = _extract_email_attachment_candidates(message)
            attachment_names = [name for name, _ in attachment_candidates]
            if not _message_matches_instrument(
                instrument=instrument,
                subject=subject,
                body_text=body_text,
                attachment_names=attachment_names,
            ):
                continue

            for attachment_name, attachment_bytes in attachment_candidates:
                rows = _parse_nav_rows_from_attachment(
                    attachment_name=attachment_name,
                    attachment_bytes=attachment_bytes,
                    parser_profile="generic_nav_table",
                )
                if not rows:
                    continue
                return replace_nav_history(
                    asset_id=asset_id,
                    rows=rows,
                    provider=f"email:{attachment_name}",
                    point_status=_normalize_import_status(rows, "complete"),
                    refresh_status="imported",
                    updated_by=updated_by,
                    message=f"Imported {len(rows)} NAV rows from email attachment {attachment_name}.",
                    mode="email",
                )

            body_rows = _parse_nav_rows_from_text(body_text)
            if body_rows:
                return replace_nav_history(
                    asset_id=asset_id,
                    rows=body_rows,
                    provider="email:body",
                    point_status=_normalize_import_status(body_rows, "complete"),
                    refresh_status="imported",
                    updated_by=updated_by,
                    message=f"Imported {len(body_rows)} NAV rows from email body.",
                    mode="email",
                )

        return update_refresh_status(
            asset_id=asset_id,
            status="no_match",
            message="No matching email NAV attachment or body table was found.",
            updated_by=updated_by,
            mode="email",
        )
    except Exception as exc:
        return update_refresh_status(
            asset_id=asset_id,
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

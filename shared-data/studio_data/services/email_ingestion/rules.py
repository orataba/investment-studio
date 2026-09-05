from __future__ import annotations

from collections.abc import Iterable
from email.utils import parseaddr
import re

from .types import MessageHeader, RouteTarget


SUPPORTED_ATTACHMENT_EXTENSIONS = frozenset({"csv", "tsv", "txt", "xls", "xlsx"})
DISCOVERY_TEXT_TOKENS = (
    "净值",
    "估值",
    "基金",
    "产品",
    "nav",
    "valuation",
)
MIN_DISCOVERY_IDENTITY_TOKEN_LENGTH = 4


def normalize_text_token(value: object) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").lower())


def normalize_email_rules(source_settings: dict[str, object]) -> tuple[dict[str, object], ...]:
    raw_rules = source_settings.get("source_email_rules", [])
    if not isinstance(raw_rules, list):
        return ()
    rules: list[dict[str, object]] = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue

        def lowered(key: str) -> list[str]:
            raw_values = raw_rule.get(key, [])
            if not isinstance(raw_values, list):
                return []
            return [
                str(item).strip().lower()
                for item in raw_values
                if str(item).strip()
            ]

        def normalized_names(key: str) -> list[str]:
            return [normalize_text_token(item) for item in lowered(key)]

        rules.append(
            {
                "sender_equals": [
                    parseaddr(item)[1].strip().lower() or item
                    for item in lowered("sender_equals")
                ],
                "subject_contains": lowered("subject_contains"),
                "subject_excludes": lowered("subject_excludes"),
                "attachment_name_contains": lowered("attachment_name_contains"),
                "attachment_name_excludes": lowered("attachment_name_excludes"),
                "attachment_extensions": [
                    item.lstrip(".") for item in lowered("attachment_extensions")
                ],
                "attachment_content_contains": lowered("attachment_content_contains"),
                "row_code_equals": [
                    item.upper() for item in lowered("row_code_equals")
                ],
                "row_name_equals": normalized_names("row_name_equals"),
                "row_name_contains": normalized_names("row_name_contains"),
                "row_name_excludes": normalized_names("row_name_excludes"),
                "parser_profile": (
                    str(raw_rule.get("parser_profile") or "generic_nav_table").strip()
                    or "generic_nav_table"
                ),
            }
        )
    return tuple(rules)


def message_matches_rule(rule: dict[str, object], header: MessageHeader) -> bool:
    normalized_subject = header.subject.lower()
    sender_equals = list(rule.get("sender_equals", []))
    if sender_equals and header.sender_email not in sender_equals:
        return False
    if any(token not in normalized_subject for token in list(rule.get("subject_contains", []))):
        return False
    if any(token in normalized_subject for token in list(rule.get("subject_excludes", []))):
        return False
    return True


def attachment_matches_rule(
    rule: dict[str, object],
    *,
    filename: str,
    searchable_text: str,
) -> bool:
    normalized_name = filename.lower()
    normalized_text = searchable_text.lower()
    required_extensions = list(rule.get("attachment_extensions", []))
    extension = normalized_name.rsplit(".", 1)[-1] if "." in normalized_name else ""
    if required_extensions and extension not in required_extensions:
        return False
    if any(token not in normalized_name for token in list(rule.get("attachment_name_contains", []))):
        return False
    if any(token in normalized_name for token in list(rule.get("attachment_name_excludes", []))):
        return False
    if any(
        token not in normalized_text
        for token in list(rule.get("attachment_content_contains", []))
    ):
        return False
    return True


def row_matches_rule(rule: dict[str, object], row: dict[str, object]) -> bool:
    row_code = str(row.get("instrument_code") or "").strip().upper()
    row_name = normalize_text_token(row.get("instrument_name"))
    required_codes = list(rule.get("row_code_equals", []))
    if required_codes and row_code not in required_codes:
        return False
    required_names = list(rule.get("row_name_equals", []))
    if required_names and row_name not in required_names:
        return False
    contains_names = list(rule.get("row_name_contains", []))
    if contains_names and not any(token and token in row_name for token in contains_names):
        return False
    if any(
        token and token in row_name
        for token in list(rule.get("row_name_excludes", []))
    ):
        return False
    return True


def has_positive_row_selector(rule: dict[str, object]) -> bool:
    return any(
        list(rule.get(key, []))
        for key in (
            "row_code_equals",
            "row_name_equals",
            "row_name_contains",
        )
    )


def has_positive_occurrence_selector(rule: dict[str, object]) -> bool:
    """Return whether a rule positively identifies a message/attachment occurrence."""
    return any(
        list(rule.get(key, []))
        for key in (
            "sender_equals",
            "subject_contains",
            "attachment_name_contains",
            "attachment_content_contains",
        )
    )


def attachment_extension(filename: str) -> str:
    normalized = filename.strip().lower()
    return normalized.rsplit(".", 1)[-1] if "." in normalized else ""


def is_supported_attachment(filename: str) -> bool:
    return attachment_extension(filename) in SUPPORTED_ATTACHMENT_EXTENSIONS


def discovery_message_candidate(
    header: MessageHeader,
    *,
    known_senders: frozenset[str],
    known_identity_tokens: frozenset[str],
) -> bool:
    if header.sender_email in known_senders:
        return True
    normalized_subject = header.subject.lower()
    if any(token in normalized_subject for token in DISCOVERY_TEXT_TOKENS):
        return True
    normalized_identity_subject = normalize_text_token(header.subject)
    return any(
        token in normalized_identity_subject for token in known_identity_tokens
    )


def discovery_identity_tokens(
    targets: Iterable[RouteTarget],
) -> frozenset[str]:
    tokens: set[str] = set()
    for target in targets:
        candidates = (target.instrument_name, *target.identifiers)
        for candidate in candidates:
            normalized = normalize_text_token(candidate)
            if len(normalized) >= MIN_DISCOVERY_IDENTITY_TOKEN_LENGTH:
                tokens.add(normalized)
    return frozenset(tokens)


def build_route_targets(instruments: Iterable[dict[str, object]]) -> list[RouteTarget]:
    targets: list[RouteTarget] = []
    for instrument in instruments:
        if str(instrument.get("instrument_type") or "").strip().lower() != "private_fund":
            continue
        lifecycle = instrument.get("lifecycle_state", {})
        if isinstance(lifecycle, dict) and lifecycle.get("status") == "archived":
            continue
        instrument_id = str(instrument.get("instrument_id") or "").strip()
        if not instrument_id:
            continue
        raw_identifiers = instrument.get("identifiers")
        identifiers = {
            str(identifier.get("identifier_value") or "").strip().upper()
            for identifier in (
                raw_identifiers if isinstance(raw_identifiers, list) else []
            )
            if isinstance(identifier, dict)
            and str(identifier.get("identifier_value") or "").strip()
        }
        identifiers.add(instrument_id.upper())
        source_settings = instrument.get("source_settings", {})
        targets.append(
            RouteTarget(
                instrument_id=instrument_id,
                instrument_name=str(instrument.get("instrument_name") or "").strip(),
                currency=str(instrument.get("currency") or "").strip().upper(),
                identifiers=frozenset(identifiers),
                rules=normalize_email_rules(
                    source_settings if isinstance(source_settings, dict) else {}
                ),
            )
        )
    return targets

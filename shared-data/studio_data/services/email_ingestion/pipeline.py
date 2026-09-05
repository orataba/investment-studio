from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
from typing import Any

from studio_data.db.session import get_session_factory

from .imap_client import ImapClient
from .repository import (
    CandidateRouteDecision,
    CandidateRouteWork,
    EmailIngestionAlreadyRunningError,
    EmailIngestionRepository,
    HISTORY_START_MESSAGE_REASON_CODE,
    history_start_message_reason,
    history_start_nav_reason,
)
from .rules import (
    attachment_matches_rule,
    attachment_extension,
    build_route_targets,
    discovery_identity_tokens,
    discovery_message_candidate,
    has_positive_occurrence_selector,
    has_positive_row_selector,
    is_supported_attachment,
    message_matches_rule,
    normalize_text_token,
    row_matches_rule,
)
from .types import (
    EmailIngestionResult,
    FolderIngestionSummary,
    InstrumentIngestionBatch,
    MessageHeader,
    RouteTarget,
)


LOGGER = logging.getLogger("investment_studio.email_ingestion")


class EmailIngestionError(RuntimeError):
    pass


class EmailIngestionBusyError(EmailIngestionError):
    pass


ParseAttachment = Callable[[str, bytes, str], list[dict[str, object]]]
ExtractAttachmentText = Callable[[str, bytes], str]


@dataclass(frozen=True)
class _RuleContext:
    target: RouteTarget
    rule: dict[str, object]
    fingerprint: str


def _rule_fingerprint(target_id: str, rule: dict[str, object]) -> str:
    payload = json.dumps(
        {"instrument_id": target_id, "rule": rule},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _configured_folders(settings: Any) -> list[str]:
    raw_folders = getattr(settings, "email_imap_folders", None)
    if isinstance(raw_folders, str):
        folders = [item.strip() for item in raw_folders.split(",") if item.strip()]
    elif isinstance(raw_folders, list):
        folders = [str(item).strip() for item in raw_folders if str(item).strip()]
    else:
        folders = []
    configured = list(dict.fromkeys(folders))
    if not configured:
        raise EmailIngestionError("At least one exact IMAP folder must be configured.")
    return configured


def _fetch_headers_with_retry(
    *,
    client: ImapClient,
    uids: Iterable[int],
    chunk_size: int,
    heartbeat: Callable[[], None],
) -> tuple[list[MessageHeader], list[int]]:
    requested = sorted(set(int(uid) for uid in uids if int(uid) > 0))
    if not requested:
        return [], []
    headers = client.fetch_headers(
        requested,
        chunk_size=chunk_size,
        on_batch=heartbeat,
        allow_missing=True,
    )
    returned_uids = {header.uid for header in headers}
    missing_uids = sorted(set(requested) - returned_uids)
    if missing_uids:
        second_attempt = client.fetch_headers(
            missing_uids,
            chunk_size=chunk_size,
            on_batch=heartbeat,
            allow_missing=True,
        )
        headers.extend(second_attempt)
        returned_uids.update(header.uid for header in second_attempt)
    headers.sort(key=lambda header: header.uid)
    return headers, sorted(set(requested) - returned_uids)


def _iter_message_payloads(
    *,
    client: ImapClient,
    uids: Iterable[int],
    chunk_size: int,
    newest_first: bool,
    heartbeat: Callable[[], None],
    on_expunged: Callable[[list[int]], None],
) -> Iterator[tuple[int, bytes]]:
    requested = sorted(
        set(int(uid) for uid in uids if int(uid) > 0),
        reverse=newest_first,
    )
    normalized_chunk_size = max(1, int(chunk_size))
    for index in range(0, len(requested), normalized_chunk_size):
        batch_uids = requested[index : index + normalized_chunk_size]
        messages = client.fetch_messages(
            batch_uids,
            chunk_size=normalized_chunk_size,
            on_batch=heartbeat,
            allow_missing=True,
        )
        missing_uids = sorted(set(batch_uids) - set(messages))
        if missing_uids:
            messages.update(
                client.fetch_messages(
                    missing_uids,
                    chunk_size=normalized_chunk_size,
                    on_batch=heartbeat,
                    allow_missing=True,
                )
            )
        confirmed_expunged = sorted(set(batch_uids) - set(messages))
        if confirmed_expunged:
            on_expunged(confirmed_expunged)
        for uid in batch_uids:
            raw_message = messages.get(uid)
            if raw_message is not None:
                yield uid, raw_message


def _known_senders(targets: Iterable[RouteTarget]) -> frozenset[str]:
    return frozenset(
        str(sender).strip().lower()
        for target in targets
        for rule in target.rules
        for sender in list(rule.get("sender_equals", []))
        if str(sender).strip()
    )


def _rule_contexts_by_uid(
    headers: Iterable[MessageHeader],
    targets: Iterable[RouteTarget],
) -> dict[int, list[_RuleContext]]:
    contexts: dict[int, list[_RuleContext]] = defaultdict(list)
    for header in headers:
        for target in targets:
            for rule in target.rules:
                if message_matches_rule(rule, header):
                    contexts[header.uid].append(
                        _RuleContext(
                            target=target,
                            rule=rule,
                            fingerprint=_rule_fingerprint(target.instrument_id, rule),
                        )
                    )
    return contexts


def _route_indexes(
    targets: Iterable[RouteTarget],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, RouteTarget]]:
    code_index: dict[str, set[str]] = defaultdict(set)
    name_index: dict[str, set[str]] = defaultdict(set)
    targets_by_id: dict[str, RouteTarget] = {}
    for target in targets:
        targets_by_id[target.instrument_id] = target
        for identifier in target.identifiers:
            code_index[identifier].add(target.instrument_id)
        normalized_name = normalize_text_token(target.instrument_name)
        if normalized_name:
            name_index[normalized_name].add(target.instrument_id)
    return code_index, name_index, targets_by_id


def _exact_target_ids(
    row: dict[str, object],
    *,
    code_index: dict[str, set[str]],
    name_index: dict[str, set[str]],
) -> set[str]:
    row_code = str(row.get("instrument_code") or "").strip().upper()
    row_name = normalize_text_token(row.get("instrument_name"))
    code_matches = set(code_index.get(row_code, set())) if row_code else set()
    name_matches = set(name_index.get(row_name, set())) if row_name else set()
    if code_matches and name_matches:
        overlap = code_matches.intersection(name_matches)
        return overlap if overlap else code_matches.union(name_matches)
    return code_matches or name_matches


def _candidate_target_ids(
    row: dict[str, object],
    *,
    rule_contexts: Iterable[_RuleContext],
    code_index: dict[str, set[str]],
    name_index: dict[str, set[str]],
) -> tuple[set[str], str | None]:
    exact_matches = _exact_target_ids(
        row,
        code_index=code_index,
        name_index=name_index,
    )
    selected_by_rule: dict[str, str] = {}
    has_identity = bool(
        str(row.get("instrument_code") or "").strip()
        or str(row.get("instrument_name") or "").strip()
    )
    for context in rule_contexts:
        if not row_matches_rule(context.rule, row):
            continue
        if has_positive_row_selector(context.rule):
            selected_by_rule[context.target.instrument_id] = context.fingerprint
        elif context.target.instrument_id in exact_matches:
            selected_by_rule[context.target.instrument_id] = context.fingerprint
        elif not has_identity and has_positive_occurrence_selector(context.rule):
            selected_by_rule[context.target.instrument_id] = context.fingerprint
    rule_matches = set(selected_by_rule)
    if rule_matches and exact_matches:
        overlap = rule_matches.intersection(exact_matches)
        selected = overlap if overlap else rule_matches.union(exact_matches)
    else:
        selected = rule_matches or exact_matches
    selected_id = next(iter(selected)) if len(selected) == 1 else None
    fingerprint = selected_by_rule.get(selected_id) if selected_id is not None else None
    return selected, fingerprint


def _parser_profiles(
    contexts: Iterable[_RuleContext],
    *,
    filename: str,
    searchable_text: str,
) -> dict[str, list[_RuleContext]]:
    normalized_contexts = list(contexts)
    profiles: dict[str, list[_RuleContext]] = defaultdict(list)
    for context in normalized_contexts:
        if attachment_matches_rule(
            context.rule,
            filename=filename,
            searchable_text=searchable_text,
        ):
            profile = str(
                context.rule.get("parser_profile") or "generic_nav_table"
            ).strip() or "generic_nav_table"
            profiles[profile].append(context)
    if not profiles and not normalized_contexts:
        profiles["generic_nav_table"] = []
    return profiles


def _with_source_metadata(
    row: dict[str, object],
    *,
    route: CandidateRouteWork,
) -> dict[str, object]:
    return {
        **row,
        "_email_candidate_route_id": route.route_id,
        "_email_parsed_candidate_id": route.parsed_candidate_id,
        "_email_folder": route.folder_name,
        "_email_uid": route.message_uid,
        "_email_sent_at": (
            route.sent_at.astimezone(UTC).isoformat()
            if route.sent_at is not None and route.sent_at.tzinfo is not None
            else route.sent_at.isoformat()
            if route.sent_at is not None
            else ""
        ),
        "_email_attachment_name": route.attachment_name,
    }


def _serialize_rule_contexts(
    contexts: Iterable[_RuleContext],
) -> list[dict[str, object]]:
    return [
        {
            "instrument_id": context.target.instrument_id,
            "rule_fingerprint": context.fingerprint,
            "rule": context.rule,
        }
        for context in contexts
    ]


def _deserialize_rule_contexts(
    raw_contexts: Iterable[dict[str, object]],
    *,
    targets_by_id: dict[str, RouteTarget],
) -> list[_RuleContext]:
    contexts: list[_RuleContext] = []
    for raw_context in raw_contexts:
        target_id = str(raw_context.get("instrument_id") or "").strip()
        target = targets_by_id.get(target_id)
        rule = raw_context.get("rule")
        fingerprint = str(raw_context.get("rule_fingerprint") or "").strip()
        if target is None or not isinstance(rule, dict) or not fingerprint:
            continue
        contexts.append(
            _RuleContext(
                target=target,
                rule=dict(rule),
                fingerprint=fingerprint,
            )
        )
    return contexts


def _valid_nav_candidate(row: dict[str, object]) -> bool:
    try:
        date.fromisoformat(str(row.get("as_of_date") or "").strip())
    except ValueError:
        return False
    has_value = False
    for key in ("nav", "cash_cumulative_nav", "nav_with_dividend"):
        raw_value = row.get(key)
        normalized = str(raw_value).strip() if raw_value is not None else ""
        if not normalized:
            continue
        has_value = True
        try:
            value = Decimal(normalized)
        except InvalidOperation:
            return False
        if not value.is_finite() or value <= 0:
            return False
    return has_value


def _route_work_item(
    *,
    route: CandidateRouteWork,
    result: EmailIngestionResult,
    code_index: dict[str, set[str]],
    name_index: dict[str, set[str]],
    targets_by_id: dict[str, RouteTarget],
    history_start_date: date,
) -> CandidateRouteDecision:
    try:
        candidate_date = date.fromisoformat(
            str(route.row.get("as_of_date") or "").strip()
        )
    except ValueError:
        candidate_date = None
    if candidate_date is not None and candidate_date < history_start_date:
        result.out_of_scope_candidates += 1
        return CandidateRouteDecision(
            route_id=route.route_id,
            status="unmatched",
            instrument_id=None,
            rule_fingerprint=None,
            validation_status="rejected",
            reason=history_start_nav_reason(history_start_date),
        )
    if not _valid_nav_candidate(route.row):
        result.invalid_candidates += 1
        return CandidateRouteDecision(
            route_id=route.route_id,
            status="unmatched",
            instrument_id=None,
            rule_fingerprint=None,
            validation_status="rejected",
            reason="NAV candidate lacks a valid ISO date or NAV value.",
        )
    rule_contexts = _deserialize_rule_contexts(
        route.routing_contexts,
        targets_by_id=targets_by_id,
    )
    target_ids, fingerprint = _candidate_target_ids(
        route.row,
        rule_contexts=rule_contexts,
        code_index=code_index,
        name_index=name_index,
    )
    if len(target_ids) != 1:
        ambiguous = len(target_ids) > 1
        if ambiguous:
            result.ambiguous_candidates += 1
        else:
            result.unmatched_candidates += 1
        return CandidateRouteDecision(
            route_id=route.route_id,
            status="ambiguous" if ambiguous else "unmatched",
            instrument_id=None,
            rule_fingerprint=fingerprint,
            reason=(
                "Candidate matched multiple Registry funds."
                if ambiguous
                else "Candidate has no exact Registry identity or occurrence rule match."
            ),
        )
    target_id = next(iter(target_ids))
    return CandidateRouteDecision(
        route_id=route.route_id,
        status="matched",
        instrument_id=target_id,
        rule_fingerprint=fingerprint,
    )


def _route_pending_parse(
    *,
    parse_id: int,
    repository: EmailIngestionRepository,
    result: EmailIngestionResult,
    code_index: dict[str, set[str]],
    name_index: dict[str, set[str]],
    targets_by_id: dict[str, RouteTarget],
    history_start_date: date,
) -> None:
    decisions = [
        _route_work_item(
            route=route,
            result=result,
            code_index=code_index,
            name_index=name_index,
            targets_by_id=targets_by_id,
            history_start_date=history_start_date,
        )
        for route in repository.pending_candidate_routes(parse_id)
    ]
    repository.route_candidates(decisions)


def _reconcile_pending_routes(
    *,
    repository: EmailIngestionRepository,
    result: EmailIngestionResult,
    code_index: dict[str, set[str]],
    name_index: dict[str, set[str]],
    targets_by_id: dict[str, RouteTarget],
    history_start_date: date,
) -> None:
    while parse_ids := repository.parse_ids_requiring_routing():
        for parse_id in parse_ids:
            _route_pending_parse(
                parse_id=parse_id,
                repository=repository,
                result=result,
                code_index=code_index,
                name_index=name_index,
                targets_by_id=targets_by_id,
                history_start_date=history_start_date,
            )


def ingest_email_nav(
    *,
    settings: Any,
    instruments: list[dict[str, object]],
    parse_attachment: ParseAttachment,
    extract_attachment_text: ExtractAttachmentText,
    full_history: bool = False,
) -> EmailIngestionResult:
    """Acquire each message once, persist raw evidence, then exact-route NAV rows."""
    if not settings.email_sync_enabled:
        raise EmailIngestionError("Email refresh is disabled.")
    if not settings.email_sync_ready:
        raise EmailIngestionError("Email refresh credentials are incomplete.")

    targets = build_route_targets(instruments)
    code_index, name_index, targets_by_id = _route_indexes(targets)
    known_senders = _known_senders(targets)
    known_identity_tokens = discovery_identity_tokens(targets)
    repository = EmailIngestionRepository(get_session_factory())
    client = ImapClient(settings)
    lease_seconds = int(getattr(settings, "email_ingestion_lease_seconds", 1800))
    try:
        lease_token = repository.acquire_mailbox_lease(
            mailbox_key=client.mailbox_key,
            lease_seconds=lease_seconds,
        )
    except EmailIngestionAlreadyRunningError as error:
        raise EmailIngestionBusyError(str(error)) from error
    try:
        return _ingest_email_nav_with_lease(
            settings=settings,
            parse_attachment=parse_attachment,
            extract_attachment_text=extract_attachment_text,
            full_history=full_history,
            targets=targets,
            code_index=code_index,
            name_index=name_index,
            targets_by_id=targets_by_id,
            known_senders=known_senders,
            known_identity_tokens=known_identity_tokens,
            repository=repository,
            client=client,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )
    finally:
        repository.release_mailbox_lease(
            mailbox_key=client.mailbox_key,
            lease_token=lease_token,
        )


def _ingest_email_nav_with_lease(
    *,
    settings: Any,
    parse_attachment: ParseAttachment,
    extract_attachment_text: ExtractAttachmentText,
    full_history: bool,
    targets: list[RouteTarget],
    code_index: dict[str, set[str]],
    name_index: dict[str, set[str]],
    targets_by_id: dict[str, RouteTarget],
    known_senders: frozenset[str],
    known_identity_tokens: frozenset[str],
    repository: EmailIngestionRepository,
    client: ImapClient,
    lease_token: str,
    lease_seconds: int,
) -> EmailIngestionResult:
    result = EmailIngestionResult()

    def heartbeat() -> None:
        repository.renew_mailbox_lease(
            mailbox_key=client.mailbox_key,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )

    # Parsing and routing are separate durable commits. Reconcile the narrow
    # crash window before acquiring more mail so a completed parse cannot be
    # stranded forever after process termination. Exact evidence that predated
    # its Registry fund is requeued here too; rejected and ambiguous evidence
    # remains fail-closed for administrator review.
    reopened_scope_routes, excluded_scope_routes = (
        repository.reconcile_candidate_route_history_start(
            settings.email_history_start_date
        )
    )
    result.out_of_scope_candidates += excluded_scope_routes
    if reopened_scope_routes:
        LOGGER.info(
            "email history scope widened requeued_routes=%s",
            reopened_scope_routes,
        )
    requeued_routes = repository.requeue_unmatched_exact_identities(
        normalized_codes=code_index,
        normalized_names=name_index,
    )
    if requeued_routes:
        LOGGER.info(
            "email exact-identity reconciliation requeued_routes=%s",
            requeued_routes,
        )
    upgraded_parse_ids = set(
        repository.prepare_parser_upgrades(
            parser_profile="label_nav_snapshot"
        )
    )
    generic_reparse_ids = repository.prepare_parser_upgrades(
        parser_profile="generic_nav_table",
        prior_statuses=("unsupported",),
    )
    upgraded_parse_ids.update(generic_reparse_ids)
    if upgraded_parse_ids:
        LOGGER.info(
            "email parser upgrade prepared parses=%s generic_terminal_retries=%s",
            len(upgraded_parse_ids),
            len(generic_reparse_ids),
        )

    # Attachment payloads are durable, so parser failures retry without
    # downloading the message again or rewinding a folder cursor.
    while due_parse_work := repository.due_parse_work():
        for work in due_parse_work:
            filename = str(work.metadata.get("file_name") or "attachment")
            if work.lease_token is None:
                raise RuntimeError(
                    "Repository returned retry work without a parse lease."
                )
            try:
                parsed_rows = parse_attachment(
                    filename,
                    work.payload,
                    work.parser_profile,
                )
                repository.complete_parse(
                    parse_id=work.parse_id,
                    lease_token=work.lease_token,
                    rows=parsed_rows,
                )
            except Exception as error:
                repository.fail_parse(
                    parse_id=work.parse_id,
                    lease_token=work.lease_token,
                    error=error,
                )
                LOGGER.exception(
                    "durable email parse retry failed parse_id=%s file=%s",
                    work.parse_id,
                    filename,
                )
                continue
            _route_pending_parse(
                parse_id=work.parse_id,
                repository=repository,
                result=result,
                code_index=code_index,
                name_index=name_index,
                targets_by_id=targets_by_id,
                history_start_date=settings.email_history_start_date,
            )
            result.rebuild_instrument_ids.update(
                repository.supersede_prior_parse_routes(parse_id=work.parse_id)
            )

    # A current parse may already exist from a previous run while only some of
    # its occurrence contexts were copied during this upgrade. Route those
    # contexts before retiring the prior-version evidence.
    for parse_id in sorted(upgraded_parse_ids):
        _route_pending_parse(
            parse_id=parse_id,
            repository=repository,
            result=result,
            code_index=code_index,
            name_index=name_index,
            targets_by_id=targets_by_id,
            history_start_date=settings.email_history_start_date,
        )
        result.rebuild_instrument_ids.update(
            repository.supersede_prior_parse_routes(
                parse_id=parse_id,
            )
        )

    _reconcile_pending_routes(
        repository=repository,
        result=result,
        code_index=code_index,
        name_index=name_index,
        targets_by_id=targets_by_id,
        history_start_date=settings.email_history_start_date,
    )

    heartbeat()
    with client:
        for folder_name in _configured_folders(settings):
            summary = FolderIngestionSummary(folder_name=folder_name)
            result.folders.append(summary)
            identity = None
            try:
                identity = client.select_folder(folder_name)
                summary.uid_validity = identity.uid_validity
                cursor = repository.start_folder_scan(
                    identity,
                    lease_token=lease_token,
                )
                discovered_uids = client.discover_uids(
                    first_uid=cursor.first_uid,
                    uid_next=identity.uid_next,
                    full_history=full_history or cursor.generation_changed,
                    history_start_date=settings.email_history_start_date,
                )
                heartbeat()
                reopened_headers, excluded_headers = (
                    repository.reconcile_message_history_start(
                        identity,
                        settings.email_history_start_date,
                    )
                )
                summary.ignored_messages += excluded_headers
                if reopened_headers:
                    LOGGER.info(
                        "email history scope widened folder=%s requeued_headers=%s",
                        folder_name,
                        reopened_headers,
                    )
                durable_headers = repository.due_message_headers(identity)
                existing_uids = repository.existing_message_uids(identity)
                new_header_uids = sorted(set(discovered_uids) - existing_uids)
                summary.discovered_messages = len(new_header_uids) + len(
                    durable_headers
                )
                header_batch_size = int(
                    getattr(settings, "email_header_fetch_batch_size", 200)
                )
                headers, confirmed_expunged = _fetch_headers_with_retry(
                    client=client,
                    uids=new_header_uids,
                    chunk_size=header_batch_size,
                    heartbeat=heartbeat,
                )
                repository.mark_messages_expunged(identity, confirmed_expunged)
                summary.failed_messages += len(confirmed_expunged)
                repository.record_headers(identity, headers)
                headers.extend(durable_headers)
                headers.sort(key=lambda header: header.uid)
                before_history_start = [
                    header
                    for header in headers
                    if header.sent_at is not None
                    and header.sent_at.date() < settings.email_history_start_date
                ]
                if before_history_start:
                    repository.mark_messages_ignored(
                        identity,
                        (header.uid for header in before_history_start),
                        reason_code=HISTORY_START_MESSAGE_REASON_CODE,
                        reason_message=history_start_message_reason(
                            settings.email_history_start_date
                        ),
                    )
                    headers = [
                        header
                        for header in headers
                        if header.sent_at is None
                        or header.sent_at.date() >= settings.email_history_start_date
                    ]
                    summary.ignored_messages += len(before_history_start)
                pending_uids = [header.uid for header in headers]
                contexts_by_uid = _rule_contexts_by_uid(headers, targets)
                candidate_headers = [
                    header
                    for header in headers
                    if contexts_by_uid.get(header.uid)
                    or discovery_message_candidate(
                        header,
                        known_senders=known_senders,
                        known_identity_tokens=known_identity_tokens,
                    )
                ]
                ignored_uids = sorted(
                    set(pending_uids) - {header.uid for header in candidate_headers}
                )
                repository.mark_messages_ignored(identity, ignored_uids)
                summary.ignored_messages += len(ignored_uids)

                candidate_uids = [header.uid for header in candidate_headers]
                message_batch_size = int(
                    getattr(settings, "email_message_fetch_batch_size", 20)
                )
                headers_by_uid = {header.uid: header for header in candidate_headers}

                def mark_expunged(uids: list[int]) -> None:
                    repository.mark_messages_expunged(identity, uids)
                    summary.failed_messages += len(uids)

                for uid, raw_message in _iter_message_payloads(
                    client=client,
                    uids=candidate_uids,
                    chunk_size=message_batch_size,
                    newest_first=full_history or cursor.generation_changed,
                    heartbeat=heartbeat,
                    on_expunged=mark_expunged,
                ):
                    header = headers_by_uid[uid]
                    try:
                        max_bytes = int(
                            getattr(settings, "email_attachment_max_bytes", 25 * 1024 * 1024)
                        )
                        (
                            all_attachments,
                            rejected_attachments,
                        ) = ImapClient.extract_attachments(
                            raw_message,
                            max_bytes=max_bytes,
                        )
                        artifact_work = repository.materialize_message(
                            identity=identity,
                            header=header,
                            raw_message=raw_message,
                            attachments=all_attachments,
                        )
                        summary.fetched_messages += 1
                        summary.stored_attachments += len(artifact_work)
                        attachments_by_part = {
                            attachment.part_id: attachment
                            for attachment in all_attachments
                        }
                        for artifact in artifact_work:
                            heartbeat()
                            attachment = attachments_by_part[artifact.part_id]
                            if (
                                hashlib.sha256(attachment.payload).hexdigest()
                                != artifact.content_sha256
                            ):
                                raise RuntimeError(
                                    "Attachment provenance did not match its artifact hash."
                                )
                            if not is_supported_attachment(attachment.filename):
                                repository.record_unsupported_attachment(
                                    artifact_id=artifact.artifact_id,
                                    source_format=attachment_extension(
                                        attachment.filename
                                    ),
                                    metadata={"file_name": attachment.filename},
                                    reason=(
                                        "Candidate email attachment type is not supported "
                                        "for automatic NAV parsing."
                                    ),
                                )
                                continue
                            rule_contexts = contexts_by_uid.get(uid, [])
                            needs_searchable_text = any(
                                list(context.rule.get("attachment_content_contains", []))
                                for context in rule_contexts
                            )
                            searchable_text = (
                                extract_attachment_text(
                                    attachment.filename,
                                    attachment.payload,
                                )
                                if needs_searchable_text
                                else ""
                            )
                            profiles = _parser_profiles(
                                rule_contexts,
                                filename=attachment.filename,
                                searchable_text=searchable_text,
                            )
                            for parser_profile, matching_contexts in profiles.items():
                                work = repository.prepare_parse(
                                    artifact_id=artifact.artifact_id,
                                    parser_profile=parser_profile,
                                    source_format=attachment_extension(
                                        attachment.filename
                                    ),
                                    metadata={
                                        "file_name": attachment.filename,
                                    },
                                )
                                if work is None:
                                    continue
                                repository.register_route_context(
                                    parse_id=work.parse_id,
                                    message_attachment_id=artifact.message_attachment_id,
                                    routing_contexts=_serialize_rule_contexts(
                                        matching_contexts
                                    ),
                                )
                                if work.disposition == "leased":
                                    if work.lease_token is None:
                                        raise RuntimeError(
                                            "Repository returned parse work without a lease."
                                        )
                                    try:
                                        rows = parse_attachment(
                                            attachment.filename,
                                            work.payload,
                                            parser_profile,
                                        )
                                        parsed_candidates = repository.complete_parse(
                                            parse_id=work.parse_id,
                                            lease_token=work.lease_token,
                                            rows=rows,
                                        )
                                        if parsed_candidates:
                                            summary.parsed_attachments += 1
                                    except Exception as error:
                                        repository.fail_parse(
                                            parse_id=work.parse_id,
                                            lease_token=work.lease_token,
                                            error=error,
                                        )
                                        LOGGER.exception(
                                            "email attachment parse failed folder=%s uid=%s file=%s profile=%s",
                                            folder_name,
                                            uid,
                                            attachment.filename,
                                            parser_profile,
                                        )
                                        continue
                                if work.disposition in ("leased", "cached"):
                                    _route_pending_parse(
                                        parse_id=work.parse_id,
                                        repository=repository,
                                        result=result,
                                        code_index=code_index,
                                        name_index=name_index,
                                        targets_by_id=targets_by_id,
                                        history_start_date=settings.email_history_start_date,
                                    )
                        if rejected_attachments:
                            repository.mark_message_attachment_rejections(
                                identity=identity,
                                uid=uid,
                                reasons=(
                                    f"{item.filename}: {item.reason}"
                                    for item in rejected_attachments
                                ),
                            )
                            summary.failed_messages += 1
                    except Exception as error:
                        summary.failed_messages += 1
                        repository.mark_message_failed(
                            identity=identity,
                            uid=uid,
                            error=error,
                        )
                        LOGGER.exception(
                            "email message materialization failed folder=%s uid=%s",
                            folder_name,
                            uid,
                        )

                # Header discovery is the durable acquisition boundary. Messages
                # that still need payload/parse work remain explicitly retryable.
                repository.finish_folder_scan(
                    identity,
                    lease_token=lease_token,
                    committed_uid=max(0, identity.uid_next - 1),
                )
                LOGGER.info(
                    "email folder ingested folder=%s uidvalidity=%s discovered=%s fetched=%s attachments=%s parsed=%s ignored=%s failed=%s",
                    folder_name,
                    identity.uid_validity,
                    summary.discovered_messages,
                    summary.fetched_messages,
                    summary.stored_attachments,
                    summary.parsed_attachments,
                    summary.ignored_messages,
                    summary.failed_messages,
                )
            except Exception as error:
                summary.error = f"{type(error).__name__}: {error}"
                if identity is not None:
                    repository.fail_folder_scan(
                        identity,
                        lease_token=lease_token,
                        error=error,
                    )
                LOGGER.exception("email folder ingestion failed folder=%s", folder_name)

    # Publication is an explicit durable outbox boundary. A route remains here
    # until the canonical writer calls ``mark_imported`` after a successful
    # transaction, so process termination cannot lose a matched observation.
    heartbeat()
    for instrument_id in sorted(result.rebuild_instrument_ids):
        target = targets_by_id.get(instrument_id)
        if target is None:
            continue
        result.batches.setdefault(
            instrument_id,
            InstrumentIngestionBatch(
                instrument_id=instrument_id,
                currency=target.currency,
            ),
        )
    for route in repository.unimported_matched_routes():
        target = targets_by_id.get(str(route.instrument_id or ""))
        if target is None:
            result.unmatched_candidates += 1
            repository.route_candidate(
                route_id=route.route_id,
                status="unmatched",
                instrument_id=None,
                rule_fingerprint=None,
                validation_status="conflict",
                reason="Previously matched Registry fund is no longer active.",
            )
            continue
        batch = result.batches.setdefault(
            target.instrument_id,
            InstrumentIngestionBatch(
                instrument_id=target.instrument_id,
                currency=target.currency,
            ),
        )
        batch.rows.append(_with_source_metadata(route.row, route=route))
        batch.attachment_names.append(route.attachment_name)
        batch.route_ids.append(route.route_id)

    return result

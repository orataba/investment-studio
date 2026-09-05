"""Investment Studio's private data CLI. No HTTP server needed."""

from __future__ import annotations

import argparse
import base64
import importlib
import inspect
from pathlib import Path
import sys
from typing import get_args, get_type_hints

from pydantic import TypeAdapter, ValidationError

from studio_data import contracts
from studio_data.commands import fx_rates, instruments, securities, status
from studio_data.services.downstream_notifications import DownstreamRefreshError
from studio_data.services.instrument_reference import (
    read_instrument_reference_data,
    refresh_instrument_reference_data,
)


def preview_nav(instrument_id: str, file: str):
    source = Path(file)
    payload = contracts.StudioNavImportPreviewRequest(
        file_name=source.name,
        file_content_base64=base64.b64encode(source.read_bytes()).decode("ascii"),
    )
    return instruments.preview_instrument_nav_history(instrument_id, payload)


def import_nav(
    instrument_id: str,
    file: str,
    provider: str = "manual",
    updated_by: str = "investment-studio-cli",
    status: str | None = None,
):
    if status is None:
        raise ValueError("Specify --status when applying a NAV file import.")
    source = Path(file)
    payload = contracts.StudioNavImportFileRequest(
        file_name=source.name,
        file_content_base64=base64.b64encode(source.read_bytes()).decode("ascii"),
        provider=provider,
        updated_by=updated_by,
        status=status,
    )
    return instruments.import_instrument_nav_history_file(instrument_id, payload)


# These are explicit operations, not arbitrary imports or arbitrary SQL access.
# The boolean marks data-changing operations requiring --apply.
COMMANDS = {
    ("instruments", "list"): (instruments.list_instrument_records, False),
    ("instruments", "show"): (instruments.get_instrument_record, False),
    ("instruments", "resolve"): (instruments.resolve_instrument_record, False),
    ("instruments", "resolve-broker"): (
        instruments.resolve_instrument_by_broker_identity,
        False,
    ),
    ("instruments", "create"): (instruments.create_instrument_record, True),
    ("instruments", "source-set"): (
        instruments.update_instrument_source_settings,
        True,
    ),
    ("instruments", "quote-policy-set"): (
        instruments.update_instrument_quote_selection_policy,
        True,
    ),
    ("instruments", "archive"): (instruments.archive_instrument_record, True),
    ("instruments", "restore"): (instruments.restore_instrument_record, True),
    ("securities", "search"): (securities.search_security_records, False),
    ("securities", "add"): (securities.materialize_security_record, True),
    ("securities", "refresh"): (securities.refresh_security_record, True),
    ("quotes", "set"): (instruments.upsert_instrument_market_data, True),
    ("fx", "list"): (fx_rates.list_shared_fx_rates, False),
    ("fx", "set"): (fx_rates.upsert_shared_fx_rate, True),
    ("nav", "preview"): (preview_nav, False),
    ("nav", "import"): (import_nav, True),
    ("nav", "import-text"): (instruments.import_instrument_nav_history, True),
    ("nav", "candidates"): (instruments.list_instrument_nav_action_candidates, False),
    ("nav", "candidate-reject"): (
        instruments.reject_instrument_nav_action_candidate,
        True,
    ),
    ("nav", "candidate-confirm"): (
        instruments.confirm_instrument_nav_action_candidate,
        True,
    ),
    ("nav", "candidate-resume"): (
        instruments.resume_instrument_nav_action_candidate_confirmation,
        True,
    ),
    ("nav", "action-create"): (instruments.create_instrument_fund_nav_action, True),
    ("nav", "action-revise"): (instruments.revise_instrument_fund_nav_action, True),
    ("nav", "evidence-create"): (
        instruments.create_instrument_fund_nav_reinvestment_evidence,
        True,
    ),
    ("nav", "evidence-revise"): (
        instruments.revise_instrument_fund_nav_reinvestment_evidence,
        True,
    ),
    ("refresh", "instrument"): (instruments.refresh_instrument_market_data, True),
    ("refresh", "batch"): (instruments.refresh_instrument_market_data_batch, True),
    ("reference", "show"): (read_instrument_reference_data, False),
    ("reference", "refresh"): (refresh_instrument_reference_data, True),
    ("status", "data"): (status.get_data_status, False),
    ("status", "email"): (status.get_email_nav_inventory, False),
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investment-studio", description=__doc__)
    roots = parser.add_subparsers(dest="area", required=True)
    data = roots.add_parser("data", help="Private data maintenance")
    groups = data.add_subparsers(dest="group", required=True)
    actions = {}
    for (group, action), (handler, writes) in COMMANDS.items():
        if group not in actions:
            actions[group] = groups.add_parser(group).add_subparsers(
                dest="action", required=True
            )
        command = actions[group].add_parser(action, description=handler.__doc__)
        command.set_defaults(handler=handler, writes=writes)
        hints = get_type_hints(handler)
        for name, parameter in inspect.signature(handler).parameters.items():
            if name == "payload":
                command.add_argument(
                    "--input", required=True, help="JSON request file, or - for stdin"
                )
                continue
            annotation = hints.get(name)
            option_type = (
                int if annotation is int or int in get_args(annotation) else str
            )
            if parameter.default is inspect.Parameter.empty:
                command.add_argument(name, type=option_type)
            elif isinstance(parameter.default, bool):
                command.add_argument(f"--{name.replace('_', '-')}", action="store_true")
            else:
                command.add_argument(
                    f"--{name.replace('_', '-')}",
                    default=parameter.default,
                    type=option_type,
                )
        if writes:
            command.add_argument(
                "--apply",
                action="store_true",
                help="Execute the data change (otherwise inspect input only)",
            )
    jobs = groups.add_parser("jobs", help="Existing scheduled refresh commands")
    jobs.add_argument("action", choices=("run", "catalogs"))
    jobs.add_argument("arguments", nargs=argparse.REMAINDER)
    csv = actions["nav"].add_parser(
        "import-csv", help="Multi-fund CSV preview/import; forwards --apply"
    )
    csv.add_argument("arguments", nargs=argparse.REMAINDER)
    return parser


def _print(value) -> None:
    print(TypeAdapter(object).dump_json(value, indent=2).decode("utf-8"))


def execute(handler, arguments: dict):
    """Validate the same typed requests used by every maintenance command."""
    values = dict(arguments)
    payload_type = get_type_hints(handler).get("payload")
    if payload_type and not isinstance(values.get("payload"), payload_type):
        values["payload"] = payload_type.model_validate(values.get("payload"))
    return handler(**values)


def _run_script(script: str, arguments: list[str]) -> int:
    previous_argv = sys.argv
    try:
        sys.argv = [script, *arguments]
        return importlib.import_module(f"scripts.{script}").main()
    finally:
        sys.argv = previous_argv


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        # The importer owns its full argument contract, including --help and
        # options before the filename. Do not partially parse them here.
        if arguments[:3] == ["data", "nav", "import-csv"]:
            return _run_script("import_registered_nav_history_csv", arguments[3:])
        args = _parser().parse_args(arguments)
        if args.group == "jobs":
            script = {
                "run": "refresh_market_data_scheduled",
                "catalogs": "refresh_release_catalogs",
            }[args.action]
            return _run_script(script, args.arguments)
        values = {
            name: getattr(args, name)
            for name in inspect.signature(args.handler).parameters
            if name != "payload"
        }
        if hasattr(args, "input"):
            raw = (
                sys.stdin.read() if args.input == "-" else Path(args.input).read_text()
            )
            values["payload"] = get_type_hints(args.handler)[
                "payload"
            ].model_validate_json(raw)
        if args.writes and not args.apply:
            if args.handler is import_nav:
                _print(preview_nav(args.instrument_id, args.file))
            else:
                _print(
                    {
                        "executed": False,
                        "input": values,
                        "message": "Input shape validated only. Add --apply to execute; database checks run on apply.",
                    }
                )
            return 0
        result = execute(args.handler, values)
        if args.group == "reference" and result is None:
            raise ValueError("Instrument not found")
        _print(result)
        if args.group == "reference" and args.action == "refresh":
            return 1 if result["section_errors"] else 0
        if args.group == "refresh":
            record = TypeAdapter(object).dump_python(result, mode="json")
            states = (
                [item["status"] for item in record["results"]]
                if args.action == "batch"
                else [record["refresh_status"].get("status")]
            )
            return 1 if any(state in {"failed", "blocked"} for state in states) else 0
        return 0
    except DownstreamRefreshError as error:
        _print(
            {
                "error": "Data saved, but downstream recalculation acknowledgement failed.",
                "detail": str(error),
            }
        )
        return 3
    except ValidationError as error:
        _print(
            {
                "error": "Invalid input",
                "details": error.errors(include_input=False, include_context=False),
            }
        )
        return 2
    except (ValueError, OSError) as error:
        _print({"error": str(error)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

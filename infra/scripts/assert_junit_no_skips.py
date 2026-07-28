#!/usr/bin/env python3
"""Fail a quality gate when a JUnit suite is empty or contains skipped tests."""

from __future__ import annotations

from pathlib import Path
import sys
import xml.etree.ElementTree as ET


def _validate_report(path: Path) -> tuple[int, list[str]]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise ValueError(f"Cannot read JUnit report {path}: {error}") from error

    cases = root.findall(".//testcase")
    if not cases:
        raise ValueError(f"JUnit suite collected zero tests: {path}")

    skipped = [
        "::".join(
            fragment
            for fragment in (case.get("classname", ""), case.get("name", "<unnamed>"))
            if fragment
        )
        for case in cases
        if case.find("skipped") is not None
    ]
    return len(cases), skipped


def main(arguments: list[str]) -> int:
    if not arguments:
        print("Usage: assert_junit_no_skips.py REPORT.xml [REPORT.xml ...]", file=sys.stderr)
        return 64

    total = 0
    violations: list[str] = []
    for raw_path in arguments:
        path = Path(raw_path)
        try:
            count, skipped = _validate_report(path)
        except ValueError as error:
            violations.append(str(error))
            continue
        total += count
        if skipped:
            violations.append(
                f"JUnit suite skipped {len(skipped)} test(s) in {path}: {', '.join(skipped)}"
            )

    if violations:
        print("PostgreSQL integration test gate failed:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    print(f"PostgreSQL integration suites executed {total} tests with zero skips.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

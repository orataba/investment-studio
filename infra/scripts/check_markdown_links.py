#!/usr/bin/env python3
"""Validate repository-local Markdown links and documentation structure."""

from __future__ import annotations

from pathlib import Path
import os
import re
import sys
from urllib.parse import unquote


LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)\n]+)\)")
FENCE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
DATE_PREFIX_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[_-]")
PROCESS_RECORD_PATTERN = re.compile(
    r"(?:^|[._ -])(?:review|handoff|evidence)(?:[._ -]|$)", re.IGNORECASE
)
IGNORED_PARTS = {
    ".git",
    ".local-pg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "backups",
    "build",
    "coverage",
    "dist",
    "htmlcov",
    "node_modules",
    "outputs",
    "ref",
    "research_outputs",
    "var",
}


def _target_path(document: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    target = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if (
        not target
        or target.startswith(("#", "/", "http://", "https://", "mailto:", "data:"))
    ):
        return None
    return (document.parent / target).resolve()


def main(arguments: list[str]) -> int:
    repository_root = Path(arguments[0] if arguments else ".").resolve()
    failures: list[str] = []
    documents = []
    for directory, children, files in os.walk(repository_root):
        parent = Path(directory)
        children[:] = [
            name for name in children
            if name not in IGNORED_PARTS and not (parent / name / ".git").exists()
        ]
        documents.extend(parent / name for name in files if name.endswith(".md"))
    documents.sort()
    document_set = {document.resolve() for document in documents}
    inbound_links: dict[Path, set[Path]] = {
        document.resolve(): set() for document in documents
    }

    for document in documents:
        relative_document = document.relative_to(repository_root)
        if "archive" in relative_document.parts:
            failures.append(
                f"documentation policy: {relative_document} is under an archive directory"
            )
        if DATE_PREFIX_PATTERN.match(document.name):
            failures.append(
                f"documentation policy: {relative_document} is a dated snapshot"
            )
        if PROCESS_RECORD_PATTERN.search(document.name):
            failures.append(
                f"documentation policy: {relative_document} is a process record"
            )

        content = FENCE_PATTERN.sub("", document.read_text(encoding="utf-8"))
        for raw_target in LINK_PATTERN.findall(content):
            target_path = _target_path(document, raw_target)
            if target_path is not None and not target_path.exists():
                failures.append(
                    f"broken link: {relative_document}: {raw_target}"
                )
            elif (
                target_path is not None
                and target_path in document_set
                and target_path != document.resolve()
            ):
                inbound_links[target_path].add(document.resolve())

    for document in documents:
        if document.resolve() == (repository_root / "README.md").resolve():
            continue
        if not inbound_links[document.resolve()]:
            failures.append(
                "undiscoverable document: "
                f"{document.relative_to(repository_root)} has no inbound Markdown link"
            )

    if failures:
        print("Markdown documentation checks failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Repository Markdown links and documentation structure are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

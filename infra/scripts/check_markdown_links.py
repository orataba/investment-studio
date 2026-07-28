#!/usr/bin/env python3
"""Validate repository-local Markdown links without network access."""

from __future__ import annotations

from pathlib import Path
import re
import sys
from urllib.parse import unquote


LINK_PATTERN = re.compile(r"!?\[[^\]]*]\(([^)\n]+)\)")
FENCE_PATTERN = re.compile(r"```.*?```", re.DOTALL)


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
    for document in sorted(repository_root.rglob("*.md")):
        if any(
            part in {".git", ".venv", "node_modules", "dist", ".pytest_cache"}
            for part in document.parts
        ):
            continue
        content = FENCE_PATTERN.sub("", document.read_text(encoding="utf-8"))
        for raw_target in LINK_PATTERN.findall(content):
            target_path = _target_path(document, raw_target)
            if target_path is not None and not target_path.exists():
                relative_document = document.relative_to(repository_root)
                failures.append(f"{relative_document}: {raw_target}")
    if failures:
        print("Broken repository-local Markdown links:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Repository-local Markdown links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

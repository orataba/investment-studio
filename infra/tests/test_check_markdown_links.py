from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_markdown_links.py"
SPEC = importlib.util.spec_from_file_location("check_markdown_links", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_accepts_indexed_living_documentation(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "[Docs](docs/README.md)\n")
    _write(tmp_path / "docs" / "README.md", "[Contract](CONTRACT.md)\n")
    _write(tmp_path / "docs" / "CONTRACT.md", "# Contract\n")

    assert MODULE.main([str(tmp_path)]) == 0


def test_submodule_owns_its_documentation_policy(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "# Parent\n")
    child = tmp_path / "apps" / "regime"
    _write(child / ".git", "gitdir: ../../.git/modules/apps/regime\n")
    _write(child / "archive" / "2026-09-04-review.md", "[Missing](none.md)\n")
    assert MODULE.main([str(tmp_path)]) == 0


def test_rejects_broken_and_undiscoverable_documents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "README.md", "[Missing](docs/MISSING.md)\n")
    _write(tmp_path / "docs" / "README.md", "# Docs\n")
    _write(tmp_path / "docs" / "ORPHAN.md", "# Orphan\n")

    assert MODULE.main([str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert "broken link" in error
    assert "undiscoverable document" in error


def test_rejects_unlinked_nested_readme(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(tmp_path / "README.md", "# Project\n")
    _write(tmp_path / "feature" / "README.md", "# Hidden feature docs\n")

    assert MODULE.main([str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert "feature/README.md has no inbound Markdown link" in error


def test_rejects_archived_dated_process_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = tmp_path / "notes" / "archive" / "2026-08-27-PROJECT-REVIEW.md"
    _write(
        tmp_path / "README.md",
        "[Old review](notes/archive/2026-08-27-PROJECT-REVIEW.md)\n",
    )
    _write(record, "# Old review\n")

    assert MODULE.main([str(tmp_path)]) == 1
    error = capsys.readouterr().err
    assert "archive directory" in error
    assert "dated snapshot" in error
    assert "process record" in error

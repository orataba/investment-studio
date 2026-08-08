#!/usr/bin/env python3
"""Check Alembic revision ancestry without connecting to the database.

The release migration runner needs to know whether a database that is already
past a prerequisite revision still requires the prerequisite phase.  Comparing
revision strings (or maintaining a list of every known descendant) is brittle
when the migration graph grows or gains a branch, so this helper asks Alembic's
revision map directly.

Exit status is zero only when every revision reported by ``alembic current`` is
the target revision itself or a descendant of it.  Unknown/empty revision
output fails closed with a non-zero status.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RangeNotAncestorError


def _revision_ids_in_output(script: ScriptDirectory, current_output: str) -> list[str]:
    known = {revision.revision for revision in script.walk_revisions()}
    # Revision identifiers are not required to be numeric.  Match tokens from
    # Alembic's output against the actual graph instead of imposing a format.
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", current_output)
    return list(dict.fromkeys(token for token in tokens if token in known))


def revisions_are_descendants(
    migration_root: Path, current_output: str, target_revision: str
) -> bool:
    config = Config(str(migration_root / "alembic.ini"))
    config.set_main_option("script_location", str(migration_root / "alembic"))
    script = ScriptDirectory.from_config(config)
    try:
        target = script.get_revision(target_revision)
    except Exception:
        return False
    if target is None:
        return False

    current_revisions = _revision_ids_in_output(script, current_output)
    if not current_revisions:
        return False

    for current_revision in current_revisions:
        try:
            # ``iterate_revisions`` raises when target is not an ancestor of
            # the current revision.  Requiring every active head to pass keeps
            # the release gate safe for a branched graph as well.
            tuple(
                script.revision_map.iterate_revisions(
                    current_revision,
                    target_revision,
                    inclusive=True,
                )
            )
        except (RangeNotAncestorError, KeyError, ValueError):
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--migration-root", type=Path, required=True)
    parser.add_argument("--current-output", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args(argv)
    return int(
        not revisions_are_descendants(
            args.migration_root,
            args.current_output,
            args.target,
        )
    )


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Stage and verify one immutable local runtime release snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path


MANIFEST_SCHEMA_VERSION = "portfolio-ops-local-runtime.v1"
MANIFEST_NAME = "runtime-manifest.json"

RUNTIME_DIRECTORIES = (
    "apps/platform/backend/platform_app",
    "apps/platform/backend/scripts",
    "apps/watchlist/backend/alembic",
    "apps/watchlist/backend/scripts",
    "apps/watchlist/backend/watchlist_app",
    "apps/watchlist/backend/watchlist_migration_snapshots",
    "apps/portfolio/backend/alembic",
    "apps/portfolio/backend/scripts",
    "apps/portfolio/backend/portfolio_app",
    "packages/instrument-core/python/portfolio_ops_instrument_core",
    "packages/calculation-core/python/portfolio_ops_calculation_core",
    "infra/instrument_registry/alembic",
    "infra/calculation_registry/alembic",
    "infra/python/portfolio_ops_infra",
)

RUNTIME_FILES = (
    "apps/watchlist/backend/alembic.ini",
    "apps/portfolio/backend/alembic.ini",
    "infra/instrument_registry/alembic.ini",
    "infra/calculation_registry/alembic.ini",
    "infra/service_inventory.sh",
    "infra/launchd/control_local_services.sh",
    "infra/launchd/generate_local_service_plists.py",
    "infra/launchd/load_runtime_env.sh",
    "infra/launchd/run_local_service.sh",
    "infra/launchd/run_market_data_refresh.sh",
    "infra/launchd/stage_local_runtime.py",
    "infra/scripts/audit_live_data.py",
    "infra/scripts/wait_for_refresh_convergence.py",
    "infra/scripts/migrate_all.sh",
    "infra/scripts/post_migration_gate.sh",
    "infra/scripts/release_database.sh",
    "infra/scripts/runtime_readiness.sh",
    "deploy/serve_spa_proxy.mjs",
)
RUNTIME_EXECUTABLE_FILES = (
    "infra/launchd/control_local_services.sh",
    "infra/launchd/run_local_service.sh",
    "infra/launchd/run_market_data_refresh.sh",
    "infra/scripts/migrate_all.sh",
    "infra/scripts/post_migration_gate.sh",
    "infra/scripts/release_database.sh",
)

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "tests",
    }
)
EXCLUDED_FILE_NAMES = frozenset({".DS_Store", ".env", ".portfolio_store.json"})
EXCLUDED_FILE_SUFFIXES = (".pyc", ".pyo")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    stage = subparsers.add_parser("stage", help="copy the runtime whitelist")
    stage.add_argument("--project-root", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)
    stage.add_argument("--release-id", required=True)

    verify = subparsers.add_parser("verify", help="verify a staged runtime")
    verify.add_argument("--runtime-root", type=Path, required=True)
    verify.add_argument("--release-id", required=True)
    return parser.parse_args()


def _is_excluded(path: Path, *, directory: bool) -> bool:
    name = path.name
    if directory:
        return name in EXCLUDED_DIRECTORY_NAMES or name.endswith(".egg-info")
    return (
        name in EXCLUDED_FILE_NAMES
        or name.startswith(".env.")
        or name.endswith(EXCLUDED_FILE_SUFFIXES)
    )


def _assert_plain_source(path: Path, *, directory: bool) -> None:
    if path.is_symlink():
        raise RuntimeError(f"runtime source must not be a symlink: {path}")
    if directory and not path.is_dir():
        raise RuntimeError(f"runtime source directory is missing: {path}")
    if not directory and not path.is_file():
        raise RuntimeError(f"runtime source file is missing: {path}")


def _copy_directory(source: Path, destination: Path) -> None:
    _assert_plain_source(source, directory=True)
    destination.mkdir(parents=True, exist_ok=False)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        is_directory = child.is_dir() and not child.is_symlink()
        if _is_excluded(child, directory=is_directory):
            continue
        if child.is_symlink():
            raise RuntimeError(f"runtime source must not contain symlinks: {child}")
        target = destination / child.name
        if child.is_dir():
            _copy_directory(child, target)
        elif child.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)
        else:
            raise RuntimeError(f"unsupported runtime source entry: {child}")
    shutil.copystat(source, destination, follow_symlinks=False)


def _copy_file(source: Path, destination: Path) -> None:
    _assert_plain_source(source, directory=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_files(runtime_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in runtime_root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"runtime snapshot contains a symlink: {path}")
        if path.is_dir():
            if _is_excluded(path, directory=True):
                raise RuntimeError(
                    f"runtime snapshot contains an excluded directory: {path}"
                )
            continue
        if not path.is_file():
            raise RuntimeError(f"runtime snapshot contains an unsupported entry: {path}")
        if _is_excluded(path, directory=False):
            raise RuntimeError(f"runtime snapshot contains an excluded file: {path}")
        if path != runtime_root / MANIFEST_NAME:
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(runtime_root).as_posix())


def _manifest_payload(runtime_root: Path, release_id: str) -> dict[str, object]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "release_id": release_id,
        "files": [
            {
                "path": path.relative_to(runtime_root).as_posix(),
                "sha256": _sha256(path),
                "mode": stat.S_IMODE(path.stat().st_mode),
            }
            for path in _runtime_files(runtime_root)
        ],
    }


def _stage(project_root: Path, destination: Path, release_id: str) -> None:
    project_root = project_root.expanduser().resolve(strict=True)
    destination = destination.expanduser()
    if not release_id or release_id.strip() != release_id or "\n" in release_id:
        raise RuntimeError("release id must be one non-empty canonical line")
    if destination.exists() or destination.is_symlink():
        raise RuntimeError(f"runtime destination already exists: {destination}")
    destination.mkdir(parents=True, mode=0o755)
    try:
        for relative in RUNTIME_DIRECTORIES:
            _copy_directory(project_root / relative, destination / relative)
        for relative in RUNTIME_FILES:
            _copy_file(project_root / relative, destination / relative)
        (destination / "release-id.txt").write_text(f"{release_id}\n", encoding="utf-8")
        manifest = _manifest_payload(destination, release_id)
        (destination / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _verify(destination, release_id)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _verify(runtime_root: Path, release_id: str) -> None:
    runtime_root = runtime_root.expanduser().resolve(strict=True)
    manifest_path = runtime_root / MANIFEST_NAME
    release_marker = runtime_root / "release-id.txt"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise RuntimeError(f"runtime manifest is missing: {manifest_path}")
    if release_marker.is_symlink() or not release_marker.is_file():
        raise RuntimeError(f"runtime release marker is missing: {release_marker}")
    if release_marker.read_text(encoding="utf-8") != f"{release_id}\n":
        raise RuntimeError("runtime release marker does not match the requested release id")
    for relative in RUNTIME_EXECUTABLE_FILES:
        executable = runtime_root / relative
        if (
            executable.is_symlink()
            or not executable.is_file()
            or (executable.stat().st_mode & 0o111) == 0
        ):
            raise RuntimeError(f"runtime executable is missing or not executable: {executable}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"runtime manifest is not valid JSON: {manifest_path}") from exc
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise RuntimeError("runtime manifest schema version is unsupported")
    if manifest.get("release_id") != release_id:
        raise RuntimeError("runtime manifest release id does not match")
    expected_files = manifest.get("files")
    if not isinstance(expected_files, list):
        raise RuntimeError("runtime manifest files must be a list")
    actual_payload = _manifest_payload(runtime_root, release_id)
    if expected_files != actual_payload["files"]:
        raise RuntimeError("runtime snapshot does not match its recorded file manifest")


def main() -> int:
    args = _parse_args()
    if args.command == "stage":
        _stage(args.project_root, args.destination, args.release_id)
    else:
        _verify(args.runtime_root, args.release_id)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

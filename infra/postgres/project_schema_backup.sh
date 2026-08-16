#!/usr/bin/env bash

# Shared, source-only primitives for taking and restoring a verified snapshot of
# the four project-owned PostgreSQL schemas. Callers remain responsible for
# stopping writers before invoking either function.

PORTFOLIO_OPS_PROJECT_SCHEMAS=(instrument_registry platform portfolio watchlist)

portfolio_ops_find_postgres_binary() {
  if [[ $# -ne 1 ]]; then
    echo "Usage: portfolio_ops_find_postgres_binary <binary>" >&2
    return 64
  fi

  local binary="$1"
  if command -v "$binary" >/dev/null 2>&1; then
    command -v "$binary"
    return
  fi
  if command -v brew >/dev/null 2>&1; then
    local formula prefix
    for formula in postgresql@18 postgresql@17 postgresql@16 postgresql; do
      if prefix="$(brew --prefix "$formula" 2>/dev/null)" \
        && [[ -x "$prefix/bin/$binary" ]]; then
        printf '%s\n' "$prefix/bin/$binary"
        return
      fi
    done
  fi
  return 1
}

portfolio_ops_prepare_libpq_connection() {
  if [[ $# -ne 2 ]]; then
    echo "Usage: portfolio_ops_prepare_libpq_connection <database-url> <private-work-dir>" >&2
    return 64
  fi

  local database_url="$1"
  local connection_dir="$2"
  local python_bin="${PYTHON_BIN:-$(command -v python3 || true)}"
  if [[ -z "$python_bin" || ! -x "$python_bin" ]]; then
    echo "Python is required to prepare a private PostgreSQL connection." >&2
    return 1
  fi

  mkdir -p "$connection_dir"
  chmod 700 "$connection_dir"
  if ! PORTFOLIO_OPS_PRIVATE_DATABASE_URL="$database_url" \
    PORTFOLIO_OPS_PRIVATE_CONNECTION_DIR="$connection_dir" \
    "$python_bin" - <<'PY'
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit


def fail(message: str) -> None:
    raise SystemExit(message)


raw_url = os.environ.pop("PORTFOLIO_OPS_PRIVATE_DATABASE_URL", "")
output_dir = Path(os.environ["PORTFOLIO_OPS_PRIVATE_CONNECTION_DIR"])
if not raw_url or "\n" in raw_url or "\r" in raw_url:
    fail("Database URL must be a non-empty single-line value.")

if raw_url.startswith("postgresql+psycopg://"):
    libpq_url = "postgresql://" + raw_url.removeprefix("postgresql+psycopg://")
elif raw_url.startswith("postgresql://"):
    libpq_url = raw_url
else:
    fail("Database URL must use postgresql:// or postgresql+psycopg://.")

try:
    parsed = urlsplit(libpq_url)
    authority_port = parsed.port
except ValueError:
    fail("Database URL has an invalid authority or port.")
if parsed.fragment:
    fail("Database URL must not contain a fragment.")

username = unquote(parsed.username or "")
if not username:
    fail("Database URL must explicitly name a database user.")
database_name = unquote(parsed.path.removeprefix("/"))
if not database_name or "/" in database_name:
    fail("Database URL must explicitly name one database.")
if any(character in username + database_name for character in "\r\n"):
    fail("Database user and database name must be single-line values.")

query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
query_options: dict[str, list[str]] = {}
for key, value in query_pairs:
    query_options.setdefault(key.lower(), []).append(value)
if query_options.get("password"):
    fail("Database passwords in URL query parameters are not supported.")
if query_options.get("user") or query_options.get("dbname"):
    fail("Database user and database name must appear once in the URL authority/path.")

authority_host = parsed.hostname or ""
query_hosts = query_options.get("host", [])
if authority_host and query_hosts:
    fail("Database host must be specified once, not in both authority and query.")
database_host = query_hosts[-1] if query_hosts else authority_host
if not database_host:
    fail("Database URL must explicitly name a host or Unix socket directory.")
if "\r" in database_host or "\n" in database_host:
    fail("Database host must be a single-line value.")

query_ports = query_options.get("port", [])
if authority_port is not None and query_ports:
    fail("Database port must be specified once, not in both authority and query.")
database_port = query_ports[-1] if query_ports else str(authority_port or 5432)
if not database_port.isdigit() or not 1 <= int(database_port) <= 65535:
    fail("Database URL has an invalid port.")

if authority_host:
    rendered_host = (
        f"[{authority_host}]" if ":" in authority_host else authority_host
    )
    rendered_port = f":{authority_port}" if authority_port is not None else ""
    netloc = f"{quote(username, safe='')}@{rendered_host}{rendered_port}"
else:
    netloc = f"{quote(username, safe='')}@"

safe_url = urlunsplit(
    (
        "postgresql",
        netloc,
        parsed.path,
        urlencode(query_pairs, doseq=True),
        "",
    )
)

values = {
    "database-url": safe_url,
    "database-name": database_name,
    "database-user": username,
    "database-host": database_host,
    "database-port": database_port,
}
for filename, value in values.items():
    target = output_dir / filename
    target.write_text(value, encoding="utf-8")
    os.chmod(target, 0o600)

password = unquote(parsed.password) if parsed.password is not None else None
if password is not None:
    if "\r" in password or "\n" in password:
        fail("Database password must be a single-line value.")
    escaped_password = password.replace("\\", "\\\\").replace(":", "\\:")
    passfile = output_dir / "pgpass"
    passfile.write_text(f"*:*:*:*:{escaped_password}\n", encoding="utf-8")
    os.chmod(passfile, 0o600)
PY
  then
    rm -rf "$connection_dir"
    return 1
  fi

  PORTFOLIO_OPS_LIBPQ_DATABASE_URL="$(< "$connection_dir/database-url")"
  PORTFOLIO_OPS_LIBPQ_DATABASE_NAME="$(< "$connection_dir/database-name")"
  PORTFOLIO_OPS_LIBPQ_DATABASE_USER="$(< "$connection_dir/database-user")"
  PORTFOLIO_OPS_LIBPQ_DATABASE_HOST="$(< "$connection_dir/database-host")"
  PORTFOLIO_OPS_LIBPQ_DATABASE_PORT="$(< "$connection_dir/database-port")"
  if [[ -f "$connection_dir/pgpass" ]]; then
    PORTFOLIO_OPS_LIBPQ_PASSFILE="$connection_dir/pgpass"
  else
    PORTFOLIO_OPS_LIBPQ_PASSFILE=""
  fi
}

portfolio_ops_run_libpq_command() {
  if [[ $# -lt 2 ]]; then
    echo "Usage: portfolio_ops_run_libpq_command <generated-passfile-or-empty> <command> [args...]" >&2
    return 64
  fi

  local passfile="$1"
  shift
  if [[ -n "$passfile" ]]; then
    env -u PGPASSWORD PGPASSFILE="$passfile" "$@"
  else
    "$@"
  fi
}

portfolio_ops_sha256() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{ print $1 }'
  else
    shasum -a 256 "$1" | awk '{ print $1 }'
  fi
}

portfolio_ops_validate_schema_manifest() {
  if [[ $# -ne 1 || ! -f "$1" ]]; then
    echo "Project-schema manifest is missing." >&2
    return 1
  fi

  local manifest_path="$1"
  local schema known candidate
  local seen=""
  while IFS= read -r schema || [[ -n "$schema" ]]; do
    [[ -n "$schema" && "$schema" != \#* ]] || continue
    known="false"
    for candidate in "${PORTFOLIO_OPS_PROJECT_SCHEMAS[@]}"; do
      if [[ "$schema" == "$candidate" ]]; then
        known="true"
        break
      fi
    done
    if [[ "$known" != "true" || "$seen" == *"|$schema|"* ]]; then
      echo "Invalid project schema in backup manifest: $schema" >&2
      return 1
    fi
    seen+="|$schema|"
  done < "$manifest_path"
}

portfolio_ops_archive_contains_manifest_schemas() {
  if [[ $# -ne 3 ]]; then
    echo "Usage: portfolio_ops_archive_contains_manifest_schemas <pg_restore> <archive> <manifest>" >&2
    return 64
  fi

  local pg_restore_bin="$1"
  local archive_path="$2"
  local manifest_path="$3"
  local archive_list schema
  archive_list="$(mktemp "${TMPDIR:-/tmp}/portfolio-ops-backup-list.XXXXXX")"
  if ! "$pg_restore_bin" --list "$archive_path" > "$archive_list"; then
    rm -f "$archive_list"
    return 1
  fi
  while IFS= read -r schema || [[ -n "$schema" ]]; do
    [[ -n "$schema" && "$schema" != \#* ]] || continue
    if ! grep -Eq "^[0-9]+; [0-9]+ [0-9]+ SCHEMA - ${schema} " "$archive_list"; then
      echo "Backup archive is missing schema declared by its manifest: $schema" >&2
      rm -f "$archive_list"
      return 1
    fi
  done < "$manifest_path"
  rm -f "$archive_list"
}

portfolio_ops_create_project_schema_backup() {
  if [[ $# -ne 3 ]]; then
    echo "Usage: portfolio_ops_create_project_schema_backup <database-url> <backup-root> <label>" >&2
    return 64
  fi

  local database_url="$1"
  local backup_root="$2"
  local label="$3"
  if [[ ! "$label" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "Backup label contains unsupported characters." >&2
    return 64
  fi

  local libpq_url passfile psql_bin pg_dump_bin pg_restore_bin work_dir schemas_path
  psql_bin="$(portfolio_ops_find_postgres_binary psql || true)"
  pg_dump_bin="$(portfolio_ops_find_postgres_binary pg_dump || true)"
  pg_restore_bin="$(portfolio_ops_find_postgres_binary pg_restore || true)"
  if [[ -z "$psql_bin" || -z "$pg_dump_bin" || -z "$pg_restore_bin" ]]; then
    echo "PostgreSQL psql, pg_dump, and pg_restore are required for schema backup." >&2
    return 1
  fi

  mkdir -p "$backup_root"
  chmod 700 "$backup_root"
  work_dir="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-project-backup.XXXXXX")"
  chmod 700 "$work_dir"
  if ! portfolio_ops_prepare_libpq_connection "$database_url" "$work_dir/connection"; then
    rm -rf "$work_dir"
    return 1
  fi
  libpq_url="$PORTFOLIO_OPS_LIBPQ_DATABASE_URL"
  passfile="$PORTFOLIO_OPS_LIBPQ_PASSFILE"
  schemas_path="$work_dir/existing-schemas"
  if ! portfolio_ops_run_libpq_command "$passfile" "$psql_bin" "$libpq_url" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "
      SELECT nspname
      FROM pg_namespace
      WHERE nspname IN ('instrument_registry', 'platform', 'portfolio', 'watchlist')
      ORDER BY nspname;
    " > "$schemas_path"; then
    rm -rf "$work_dir"
    return 1
  fi

  local timestamp base backup_path manifest_path checksum_path manifest_checksum_path schema
  local schema_args=()
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  base="$backup_root/${label}-${timestamp}-$$"
  backup_path="$base.pgdump"
  manifest_path="$base.schemas"
  checksum_path="$base.pgdump.sha256"
  manifest_checksum_path="$base.schemas.sha256"
  : > "$manifest_path"
  chmod 600 "$manifest_path"
  while IFS= read -r schema || [[ -n "$schema" ]]; do
    [[ -n "$schema" ]] || continue
    case "$schema" in
      instrument_registry|platform|portfolio|watchlist) ;;
      *)
        echo "Database returned an unexpected project schema name: $schema" >&2
        rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
        return 1
        ;;
    esac
    printf '%s\n' "$schema" >> "$manifest_path"
    schema_args+=("--schema=$schema")
  done < "$schemas_path"

  if ! portfolio_ops_validate_schema_manifest "$manifest_path"; then
    rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
    return 1
  fi

  if [[ ${#schema_args[@]} -gt 0 ]]; then
    if ! portfolio_ops_run_libpq_command "$passfile" "$pg_dump_bin" \
      --dbname "$libpq_url" \
      --format=custom \
      --no-owner \
      --no-acl \
      "${schema_args[@]}" \
      --file "$backup_path"; then
      rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
      return 1
    fi
    chmod 600 "$backup_path"
    if ! portfolio_ops_archive_contains_manifest_schemas \
      "$pg_restore_bin" "$backup_path" "$manifest_path"; then
      rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
      return 1
    fi
    local checksum
    checksum="$(portfolio_ops_sha256 "$backup_path")" || {
      rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
      return 1
    }
    printf '%s  %s\n' "$checksum" "$(basename "$backup_path")" > "$checksum_path"
    chmod 600 "$checksum_path"
    if [[ "$(portfolio_ops_sha256 "$backup_path")" != "$checksum" ]]; then
      echo "Project-schema backup checksum verification failed." >&2
      rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
      return 1
    fi
  else
    printf '%s\n' '# No project schemas existed before this operation.' > "$manifest_path"
    backup_path=""
    checksum_path=""
  fi

  local manifest_checksum
  manifest_checksum="$(portfolio_ops_sha256 "$manifest_path")" || {
    rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
    return 1
  }
  printf '%s  %s\n' "$manifest_checksum" "$(basename "$manifest_path")" \
    > "$manifest_checksum_path"
  chmod 600 "$manifest_checksum_path"
  if [[ "$(portfolio_ops_sha256 "$manifest_path")" != "$manifest_checksum" ]]; then
    echo "Project-schema manifest checksum verification failed." >&2
    rm -rf "$work_dir" "$backup_path" "$manifest_path" "$checksum_path" "$manifest_checksum_path"
    return 1
  fi

  rm -rf "$work_dir"
  PORTFOLIO_OPS_PROJECT_SCHEMA_BACKUP_PATH="$backup_path"
  PORTFOLIO_OPS_PROJECT_SCHEMA_MANIFEST_PATH="$manifest_path"
  PORTFOLIO_OPS_PROJECT_SCHEMA_CHECKSUM_PATH="$checksum_path"
  PORTFOLIO_OPS_PROJECT_SCHEMA_MANIFEST_CHECKSUM_PATH="$manifest_checksum_path"
  echo "Verified pre-migration project-schema backup: ${backup_path:-$manifest_path}"
}

portfolio_ops_restore_project_schema_backup() (
  if [[ $# -ne 3 ]]; then
    echo "Usage: portfolio_ops_restore_project_schema_backup <database-url> <archive-or-empty> <manifest>" >&2
    return 64
  fi

  local database_url="$1"
  local backup_path="$2"
  local manifest_path="$3"
  local libpq_url passfile psql_bin pg_restore_bin work_dir
  psql_bin="$(portfolio_ops_find_postgres_binary psql || true)"
  pg_restore_bin="$(portfolio_ops_find_postgres_binary pg_restore || true)"
  if [[ -z "$psql_bin" || -z "$pg_restore_bin" ]]; then
    echo "PostgreSQL psql and pg_restore are required for schema rollback." >&2
    return 1
  fi
  work_dir="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-project-rollback.XXXXXX")"
  chmod 700 "$work_dir"
  trap 'rm -rf "$work_dir"' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
  portfolio_ops_prepare_libpq_connection "$database_url" "$work_dir/connection" || return
  libpq_url="$PORTFOLIO_OPS_LIBPQ_DATABASE_URL"
  passfile="$PORTFOLIO_OPS_LIBPQ_PASSFILE"
  if [[ ! -f "$manifest_path.sha256" ]]; then
    echo "Project-schema manifest checksum is missing." >&2
    return 1
  fi
  local expected_manifest_checksum actual_manifest_checksum
  expected_manifest_checksum="$(awk 'NF { print $1; exit }' "$manifest_path.sha256")"
  actual_manifest_checksum="$(portfolio_ops_sha256 "$manifest_path")" || return
  if [[ ! "$expected_manifest_checksum" =~ ^[0-9A-Fa-f]{64}$ \
    || "$actual_manifest_checksum" != "$expected_manifest_checksum" ]]; then
    echo "Project-schema manifest checksum verification failed." >&2
    return 1
  fi
  portfolio_ops_validate_schema_manifest "$manifest_path" || return

  local manifest_has_schemas="false"
  local schema expected_checksum actual_checksum checksum_path
  while IFS= read -r schema || [[ -n "$schema" ]]; do
    [[ -n "$schema" && "$schema" != \#* ]] || continue
    manifest_has_schemas="true"
  done < "$manifest_path"

  if [[ "$manifest_has_schemas" == "true" ]]; then
    if [[ -z "$backup_path" || ! -f "$backup_path" ]]; then
      echo "Project-schema backup archive is missing." >&2
      return 1
    fi
    checksum_path="$backup_path.sha256"
    if [[ ! -f "$checksum_path" ]]; then
      echo "Project-schema backup checksum is missing." >&2
      return 1
    fi
    expected_checksum="$(awk 'NF { print $1; exit }' "$checksum_path")"
    actual_checksum="$(portfolio_ops_sha256 "$backup_path")" || return
    if [[ ! "$expected_checksum" =~ ^[0-9A-Fa-f]{64}$ \
      || "$actual_checksum" != "$expected_checksum" ]]; then
      echo "Project-schema backup checksum verification failed." >&2
      return 1
    fi
    portfolio_ops_archive_contains_manifest_schemas \
      "$pg_restore_bin" "$backup_path" "$manifest_path" || return
  elif [[ -n "$backup_path" ]]; then
    echo "Backup archive was provided for an empty project-schema manifest." >&2
    return 1
  fi

  if ! (
    printf '%s\n' '
        DROP SCHEMA IF EXISTS watchlist CASCADE;
        DROP SCHEMA IF EXISTS portfolio CASCADE;
        DROP SCHEMA IF EXISTS platform CASCADE;
        DROP SCHEMA IF EXISTS instrument_registry CASCADE;
      '
    if [[ "$manifest_has_schemas" == "true" ]]; then
      exec "$pg_restore_bin" \
        --file - \
        --no-owner \
        --no-acl \
        "$backup_path"
    fi
    exit 0
  ) | portfolio_ops_run_libpq_command "$passfile" "$psql_bin" "$libpq_url" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --single-transaction; then
    echo "Atomic project-schema rollback failed; the pre-rollback schemas were preserved." >&2
    return 1
  fi

  local restored_schemas actual_schema_list expected_schema_list
  restored_schemas="$(mktemp "${TMPDIR:-/tmp}/portfolio-ops-restored-schemas.XXXXXX")"
  if ! portfolio_ops_run_libpq_command "$passfile" "$psql_bin" "$libpq_url" \
    --no-password \
    --set ON_ERROR_STOP=1 \
    --tuples-only \
    --no-align \
    --command "
      SELECT nspname
      FROM pg_namespace
      WHERE nspname IN ('instrument_registry', 'platform', 'portfolio', 'watchlist')
      ORDER BY nspname;
    " > "$restored_schemas"; then
    rm -f "$restored_schemas"
    return 1
  fi
  actual_schema_list="$(awk 'NF && $0 !~ /^#/ { print }' "$restored_schemas" | sort)"
  expected_schema_list="$(awk 'NF && $0 !~ /^#/ { print }' "$manifest_path" | sort)"
  rm -f "$restored_schemas"
  if [[ "$actual_schema_list" != "$expected_schema_list" ]]; then
    echo "Post-rollback project-schema validation failed." >&2
    return 1
  fi
  echo "Pre-migration project schemas restored successfully." >&2
)

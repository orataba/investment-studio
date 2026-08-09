#!/usr/bin/env bash

# Shared runtime-environment validation for managed service installers and
# runners. Dotenv assignments are parsed as data, never sourced as shell code.

portfolio_ops_require_password_free_database_url() {
  if [[ $# -ne 1 ]]; then
    echo "Usage: portfolio_ops_require_password_free_database_url <database-url>" >&2
    return 64
  fi
  local database_url="$1"
  local authority userinfo
  authority="${database_url#*://}"
  authority="${authority%%/*}"
  if [[ "$authority" == *"@"* ]]; then
    userinfo="${authority%@*}"
    if [[ "$userinfo" == *":"* ]]; then
      echo "Database URLs for managed services must not contain passwords; use a 0600 .pgpass file." >&2
      return 64
    fi
  fi
}

portfolio_ops_sqlalchemy_database_url() {
  if [[ $# -ne 1 ]]; then
    echo "Usage: portfolio_ops_sqlalchemy_database_url <database-url>" >&2
    return 64
  fi
  local database_url="$1"
  case "$database_url" in
    postgresql://*)
      printf 'postgresql+psycopg://%s\n' "${database_url#postgresql://}"
      ;;
    postgresql+psycopg://*)
      printf '%s\n' "$database_url"
      ;;
    *)
      echo "Database URL must use postgresql:// or postgresql+psycopg://." >&2
      return 64
      ;;
  esac
}

portfolio_ops_reject_repository_env_files() {
  if [[ $# -ne 1 ]]; then
    echo "Usage: portfolio_ops_reject_repository_env_files <project-root>" >&2
    return 64
  fi

  local project_root="$1"
  local app repository_env
  for app in platform watchlist portfolio; do
    repository_env="$project_root/apps/$app/backend/.env"
    if [[ -e "$repository_env" || -L "$repository_env" ]]; then
      echo "Repository runtime environment files are not allowed for managed services: $repository_env" >&2
      echo "Move the values to the matching external environment file and remove the repository file or symlink." >&2
      return 1
    fi
  done
}

portfolio_ops_resolve_external_env_root() {
  if [[ $# -ne 2 ]]; then
    echo "Usage: portfolio_ops_resolve_external_env_root <project-root> <external-env-root>" >&2
    return 64
  fi

  local project_root="$1"
  local external_env_root="$2"
  local resolved_project_root resolved_env_root root_mode root_owner
  if [[ -z "$external_env_root" || "$external_env_root" == *$'\n'* || "$external_env_root" == *$'\r'* ]]; then
    echo "An external runtime environment root is required." >&2
    return 64
  fi
  if [[ ! -d "$external_env_root" ]]; then
    echo "External runtime environment root does not exist: $external_env_root" >&2
    return 1
  fi

  resolved_project_root="$(cd "$project_root" && pwd -P)"
  resolved_env_root="$(cd "$external_env_root" && pwd -P)"
  case "$resolved_env_root/" in
    "$resolved_project_root/"*)
      echo "Runtime environment root must be outside the repository: $resolved_env_root" >&2
      return 1
      ;;
  esac

  if root_mode="$(stat -f '%Lp' "$resolved_env_root" 2>/dev/null)"; then
    root_owner="$(stat -f '%u' "$resolved_env_root")"
  else
    root_mode="$(stat -c '%a' "$resolved_env_root")"
    root_owner="$(stat -c '%u' "$resolved_env_root")"
  fi
  if (( (8#$root_mode & 8#077) != 0 )); then
    echo "Runtime environment directory must not be accessible by group or others: $resolved_env_root" >&2
    return 1
  fi
  if [[ "$root_owner" != "$UID" ]]; then
    echo "Runtime environment directory must be owned by uid $UID: $resolved_env_root" >&2
    return 1
  fi

  printf '%s\n' "$resolved_env_root"
}

portfolio_ops_runtime_env_file() {
  if [[ $# -ne 2 ]]; then
    echo "Usage: portfolio_ops_runtime_env_file <app> <external-env-root>" >&2
    return 64
  fi

  local app="$1"
  local external_env_root="$2"
  local external_file="$external_env_root/$app.env"

  # Runtime secrets have one authoritative location outside the worktree.
  printf '%s\n' "$external_file"
}

portfolio_ops_validate_env_file() {
  if [[ $# -lt 2 ]]; then
    echo "Usage: portfolio_ops_validate_env_file <env-file> <allowed-prefix> [allowed-prefix ...]" >&2
    return 64
  fi

  local env_file="$1"
  shift
  local allowed_prefixes=("$@")
  local file_mode file_owner parent_dir parent_mode parent_owner

  if [[ ! -f "$env_file" ]]; then
    echo "Missing runtime environment file: $env_file" >&2
    return 1
  fi

  parent_dir="$(cd "$(dirname "$env_file")" && pwd)"

  if file_mode="$(stat -f '%Lp' "$env_file" 2>/dev/null)"; then
    file_owner="$(stat -f '%u' "$env_file")"
    parent_mode="$(stat -f '%Lp' "$parent_dir")"
    parent_owner="$(stat -f '%u' "$parent_dir")"
  else
    file_mode="$(stat -c '%a' "$env_file")"
    file_owner="$(stat -c '%u' "$env_file")"
    parent_mode="$(stat -c '%a' "$parent_dir")"
    parent_owner="$(stat -c '%u' "$parent_dir")"
  fi
  if (( (8#$parent_mode & 8#077) != 0 )); then
    echo "Runtime environment directory must not be accessible by group or others: $parent_dir" >&2
    return 1
  fi
  if [[ "$parent_owner" != "$UID" ]]; then
    echo "Runtime environment directory must be owned by uid $UID: $parent_dir" >&2
    return 1
  fi
  if (( (8#$file_mode & 8#077) != 0 )); then
    echo "Runtime environment file must not be accessible by group or others: $env_file" >&2
    return 1
  fi
  if [[ "$file_owner" != "$UID" ]]; then
    echo "Runtime environment file must be owned by uid $UID: $env_file" >&2
    return 1
  fi

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line="${raw_line%$'\r'}"
    local key allowed_prefix prefix_matches
    if [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi
    if [[ "$line" != *"="* ]]; then
      echo "Invalid environment entry in $env_file." >&2
      return 1
    fi
    key="${line%%=*}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    prefix_matches="false"
    for allowed_prefix in "${allowed_prefixes[@]}"; do
      if [[ "$key" == "$allowed_prefix"* ]]; then
        prefix_matches="true"
        break
      fi
    done
    if [[ ! "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ || "$prefix_matches" != "true" ]]; then
      echo "Environment key is outside the allowed namespace: $key" >&2
      return 1
    fi
  done < "$env_file"
}

portfolio_ops_load_env_file() {
  if [[ $# -lt 2 ]]; then
    echo "Usage: portfolio_ops_load_env_file <env-file> <allowed-prefix> [allowed-prefix ...]" >&2
    return 64
  fi

  local env_file="$1"
  shift
  local allowed_prefixes=("$@")
  portfolio_ops_validate_env_file "$env_file" "${allowed_prefixes[@]}" || return

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line="${raw_line%$'\r'}"
    local key value
    if [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]]; then
      continue
    fi

    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"

    # Values explicitly supplied by the process manager or caller win over the
    # file.  Quoted dotenv values are unwrapped but never evaluated or expanded.
    if [[ ${!key+x} ]]; then
      continue
    fi
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 ]]; then
      if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]]; then
        value="${value:1:${#value}-2}"
      elif [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
        value="${value:1:${#value}-2}"
      fi
    fi
    export "$key=$value"
  done < "$env_file"
}

#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$REPOSITORY_ROOT/.venv/bin/python}"
TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/portfolio-ops-junit-gate.XXXXXX")"
trap 'rm -rf "$TEST_ROOT"' EXIT

cat > "$TEST_ROOT/passed.xml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="1" failures="0" errors="0" skipped="0">
  <testsuite name="passed" tests="1" failures="0" errors="0" skipped="0">
    <testcase classname="suite" name="executes" />
  </testsuite>
</testsuites>
XML

cat > "$TEST_ROOT/skipped.xml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="1" failures="0" errors="0" skipped="1">
  <testsuite name="skipped" tests="1" failures="0" errors="0" skipped="1">
    <testcase classname="suite" name="must_not_skip"><skipped message="database unavailable" /></testcase>
  </testsuite>
</testsuites>
XML

cat > "$TEST_ROOT/empty.xml" <<'XML'
<?xml version="1.0" encoding="utf-8"?>
<testsuites tests="0" failures="0" errors="0" skipped="0" />
XML

cat > "$TEST_ROOT/malformed.xml" <<'XML'
<testsuites><testsuite><testcase>
XML

"$PYTHON_BIN" "$REPOSITORY_ROOT/infra/scripts/assert_junit_no_skips.py" "$TEST_ROOT/passed.xml"

if "$PYTHON_BIN" "$REPOSITORY_ROOT/infra/scripts/assert_junit_no_skips.py" \
  "$TEST_ROOT/skipped.xml" >"$TEST_ROOT/skipped.out" 2>"$TEST_ROOT/skipped.err"; then
  echo "JUnit gate unexpectedly accepted a skipped test." >&2
  exit 1
fi
grep -q 'skipped 1 test' "$TEST_ROOT/skipped.err"

if "$PYTHON_BIN" "$REPOSITORY_ROOT/infra/scripts/assert_junit_no_skips.py" \
  "$TEST_ROOT/empty.xml" >"$TEST_ROOT/empty.out" 2>"$TEST_ROOT/empty.err"; then
  echo "JUnit gate unexpectedly accepted an empty suite." >&2
  exit 1
fi
grep -q 'collected zero tests' "$TEST_ROOT/empty.err"

if "$PYTHON_BIN" "$REPOSITORY_ROOT/infra/scripts/assert_junit_no_skips.py" \
  "$TEST_ROOT/malformed.xml" >"$TEST_ROOT/malformed.out" 2>"$TEST_ROOT/malformed.err"; then
  echo "JUnit gate unexpectedly accepted malformed XML." >&2
  exit 1
fi
grep -q 'Cannot read JUnit report' "$TEST_ROOT/malformed.err"

if "$PYTHON_BIN" "$REPOSITORY_ROOT/infra/scripts/assert_junit_no_skips.py" \
  "$TEST_ROOT/missing.xml" >"$TEST_ROOT/missing.out" 2>"$TEST_ROOT/missing.err"; then
  echo "JUnit gate unexpectedly accepted a missing report." >&2
  exit 1
fi
grep -q 'Cannot read JUnit report' "$TEST_ROOT/missing.err"

"$PYTHON_BIN" "$REPOSITORY_ROOT/infra/scripts/assert_junit_no_skips.py" \
  "$TEST_ROOT/passed.xml" "$TEST_ROOT/passed.xml" \
  | grep -q 'executed 2 tests with zero skips'

echo "JUnit no-skip gate test passed."

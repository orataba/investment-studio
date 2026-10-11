import pytest

from tests.test_postgres_schema_reconciliation import postgres_reconciliation_database, _portfolio_config  # noqa: F401
from tests.test_strict_return_policy_migration import exercise_migration

pytestmark = pytest.mark.postgresql_integration


def test_postgres_strict_policy_upgrade_constraints_and_history(postgres_reconciliation_database):
    exercise_migration(_portfolio_config(postgres_reconciliation_database))

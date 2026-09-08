import pytest
import sqlalchemy as sa
from portfolio_app.db.session import get_engine
from tests.conftest import _run_alembic_upgrade


@pytest.mark.migration_base_revision('20260906_0060')
def test_accounts_migration_preserves_ledger_and_requires_explicit_initial_grants():
    engine = get_engine()
    with engine.connect() as connection:
        before = connection.execute(sa.text('SELECT transaction_id, gross_amount, trade_date FROM transaction_record ORDER BY transaction_id')).all()
        portfolio_ids = connection.execute(sa.text('SELECT portfolio_id FROM portfolio_record ORDER BY portfolio_id')).all()
    _run_alembic_upgrade(str(engine.url), '20260908_0061')
    with engine.connect() as connection:
        assert connection.execute(sa.text('SELECT transaction_id, gross_amount, trade_date FROM transaction_record ORDER BY transaction_id')).all() == before
        assert connection.execute(sa.text('SELECT portfolio_id FROM portfolio_record ORDER BY portfolio_id')).all() == portfolio_ids
        assert connection.execute(sa.text('SELECT COUNT(*) FROM portfolio_membership')).scalar_one() == 0
        assert connection.execute(sa.text('SELECT COUNT(*) FROM portfolio_access_state')).scalar_one() == 0
        assert {'actor_user_id', 'actor_name'} <= {item['name'] for item in sa.inspect(connection).get_columns('transaction_change_log')}

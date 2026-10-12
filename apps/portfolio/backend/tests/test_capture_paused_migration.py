"""The narrow status migration retains existing rows; downgrade never relabels pauses."""
from alembic import command
import pytest
import sqlalchemy as sa
from portfolio_app.db.session import get_engine
from tests.test_migrations import _alembic_config
from tests.test_transaction_captures_api import _upload_capture, _create_batch


@pytest.mark.migration_base_revision('20261008_0072')
def test_capture_pause_constraint_upgrade_preserves_originals_and_refuses_lossy_downgrade(client):
    capture=_upload_capture(client);batch=_create_batch(client,[capture['capture_id']])
    query=sa.text('SELECT * FROM transaction_capture_batch WHERE batch_id=:id')
    with get_engine().connect() as conn:before=dict(conn.execute(query,{'id':batch['batch_id']}).mappings().one())
    with pytest.raises(sa.exc.IntegrityError),get_engine().begin() as conn:
        conn.execute(sa.text("UPDATE transaction_capture_batch SET analysis_run_status='paused' WHERE batch_id=:id"),{'id':batch['batch_id']})
    command.upgrade(_alembic_config(),'20261012_0073')
    with get_engine().connect() as conn:assert dict(conn.execute(query,{'id':batch['batch_id']}).mappings().one())==before
    with get_engine().begin() as conn:
        conn.execute(sa.text("UPDATE transaction_capture_batch SET analysis_run_status='paused' WHERE batch_id=:id"),{'id':batch['batch_id']})
    with pytest.raises(RuntimeError,match='cannot preserve'):
        command.downgrade(_alembic_config(),'20261008_0072')
    with get_engine().connect() as conn:
        after=dict(conn.execute(query,{'id':batch['batch_id']}).mappings().one())
    assert after=={**before,'analysis_run_status':'paused'}
    assert client.get(f"/api/portfolios/investment-studio/transaction-capture-batches/{batch['batch_id']}").json()['captures']==batch['captures']

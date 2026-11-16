import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import engine


@pytest.fixture
def db():
    """A session on the local Docker Postgres inside a transaction that is rolled back; skipped when it isn't up."""
    try:
        conn = engine.connect()
    except OperationalError:
        pytest.skip("local Postgres not running")
    tx = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield s
    s.close()
    tx.rollback()
    conn.close()

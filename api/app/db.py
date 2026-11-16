from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Neon suspends the compute when idle and drops idle connections, so ping before handing a
# connection out and recycle well inside its timeout.
connect_args: dict = {}
if "-pooler" in settings.database_url:
    # The pooled endpoint runs PgBouncer in transaction mode, where psycopg3's prepared
    # statements fail with "prepared statement already exists". .env should hold the direct
    # endpoint instead; this keeps the pooled one working rather than failing at the 6th query.
    connect_args["prepare_threshold"] = None

engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=300, connect_args=connect_args)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session

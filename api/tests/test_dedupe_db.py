"""Runs against the local Docker Postgres; skipped when it isn't up. Writes inside a rolled-back transaction."""

import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.db import engine
from app.dedupe import collapse_across_owners
from app.models import Media

T0 = datetime(2020, 1, 1, 12, tzinfo=timezone.utc)  # before any real media


@pytest.fixture
def session():
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


def _vec(*head):
    v = np.zeros(512, dtype=np.float32)
    v[: len(head)] = head
    return (v / np.linalg.norm(v)).tolist()


def _media(owner, at, emb, sharp):
    sha = uuid.uuid4().hex * 2
    return Media(sha256=sha, source_path=sha, owner=owner, kind="photo", taken_at=at,
                 embedding=emb, sharpness=sharp, dup_group=sha[:16], is_dup_primary=True, tags={})


def test_same_moment_from_two_rolls_keeps_sharper(session):
    mine = _media("owner", T0, _vec(1, 0), 10.0)
    hers = _media("partner", T0 + timedelta(seconds=30), _vec(1, 0.02), 40.0)
    other_shot = _media("partner", T0 + timedelta(seconds=40), _vec(0, 1), 5.0)
    later = _media("partner", T0 + timedelta(minutes=30), _vec(1, 0), 5.0)
    session.add_all([mine, hers, other_shot, later])
    session.flush()

    assert collapse_across_owners(session) == 1
    primary = {m.id: m.is_dup_primary for m in session.scalars(select(Media).where(Media.taken_at < T0 + timedelta(hours=1)))}
    assert primary == {mine.id: False, hers.id: True, other_shot.id: True, later.id: True}

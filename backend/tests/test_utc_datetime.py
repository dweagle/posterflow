from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import Column, Integer, create_engine, select
from sqlalchemy.orm import Session, declarative_base
from sqlalchemy.pool import StaticPool

from util.utc_datetime import UTCDateTime

Base = declarative_base()


class Sample(Base):
    __tablename__ = "sample_utc_datetime"

    id = Column(Integer, primary_key=True)
    stamp = Column(UTCDateTime, nullable=True)


@pytest.fixture
def session():
    # StaticPool keeps one connection alive so the in-memory DB survives the session.
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        yield s
    Base.metadata.drop_all(bind=engine)


def test_aware_utc_round_trips_aware(session):
    session.add(Sample(stamp=datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)))
    session.commit()

    stored = session.execute(select(Sample)).scalar_one()

    assert stored.stamp == datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)
    assert stored.stamp.tzinfo is not None


def test_aware_non_utc_is_normalised_to_utc(session):
    """14:30 Amsterdam is 12:30 UTC. Before UTCDateTime this stored 14:30 raw — a silent 2h skew."""
    session.add(Sample(stamp=datetime(2026, 9, 30, 14, 30, tzinfo=ZoneInfo("Europe/Amsterdam"))))
    session.commit()

    stored = session.execute(select(Sample)).scalar_one()

    assert stored.stamp == datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc)


def test_naive_bind_is_treated_as_utc(session):
    """Every existing row in a live install is naive UTC; binds must keep meaning the same."""
    session.add(Sample(stamp=datetime(2026, 9, 30, 14, 30)))
    session.commit()

    stored = session.execute(select(Sample)).scalar_one()

    assert stored.stamp == datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)


def test_none_round_trips_as_none(session):
    session.add(Sample(stamp=None))
    session.commit()

    assert session.execute(select(Sample)).scalar_one().stamp is None

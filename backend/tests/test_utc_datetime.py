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
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as s:
        yield s
    engine.dispose()


def _round_trip(session, stamp):
    session.add(Sample(stamp=stamp))
    session.commit()
    session.expire_all()
    return session.execute(select(Sample)).scalar_one().stamp


def test_aware_utc_round_trips_aware(session):
    stamp = datetime(2026, 9, 30, 14, 30, 0, 123456, tzinfo=timezone.utc)

    stored = _round_trip(session, stamp)

    assert stored == stamp
    assert stored.tzinfo is not None


def test_aware_non_utc_is_stored_as_utc(session):
    stored = _round_trip(session, datetime(2026, 9, 30, 14, 30, tzinfo=ZoneInfo("Europe/Amsterdam")))

    assert stored == datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc)
    assert stored.utcoffset().total_seconds() == 0


def test_naive_value_is_read_as_utc(session):
    """Rows written before this type are naive UTC and must keep meaning the same instant."""
    stored = _round_trip(session, datetime(2026, 9, 30, 14, 30))

    assert stored == datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)


def test_none_round_trips_as_none(session):
    assert _round_trip(session, None) is None


def test_aware_bound_filters_against_stored_utc(session):
    _round_trip(session, datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc))
    cutoff = datetime(2026, 9, 30, 14, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))

    assert session.execute(select(Sample).where(Sample.stamp >= cutoff)).scalar_one_or_none() is not None
    assert session.execute(select(Sample).where(Sample.stamp < cutoff)).scalar_one_or_none() is None

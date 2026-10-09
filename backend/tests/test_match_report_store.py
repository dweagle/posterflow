"""Tests for the in-memory match report hand-off."""
from services import match_report_store as store


def setup_function() -> None:
    store.clear_reports()


def test_store_and_read_back():
    store.store_report(5, {"report_text": "hello"}, now=100.0)
    assert store.get_report(5, now=200.0) == {"report_text": "hello"}
    assert store.get_report(6, now=200.0) is None


def test_reports_expire_after_ttl():
    store.store_report(5, {"report_text": "hello"}, now=100.0)
    assert store.get_report(5, now=100.0 + store.REPORT_TTL_SECONDS - 1) is not None
    assert store.get_report(5, now=100.0 + store.REPORT_TTL_SECONDS) is None


def test_storing_prunes_stale_entries():
    store.store_report(1, {"a": 1}, now=0.0)
    store.store_report(2, {"b": 2}, now=store.REPORT_TTL_SECONDS + 10)
    assert store.get_report(1, now=store.REPORT_TTL_SECONDS + 10) is None
    assert store.get_report(2, now=store.REPORT_TTL_SECONDS + 10) == {"b": 2}

"""The live-log broadcast hub: loguru sink → per-client queues → WS batch frames.

These pin the push pipeline that replaced file tailing: entries fan out to every
subscriber, a stalled client drops its oldest lines instead of blocking logging,
and the sink emits the exact frame shape the Logs page speaks (skipping the raw
blank spacers that exist only for file readability).

The last two tests pin that every stamp is UTC rather than host-local. Both changed
paths convert on their own — the stream because its sink uses format="{message}" and
so never sees loguru's !UTC, the files because of the !UTC in LOGURU_TIMESTAMP_FORMAT
— and the shape assertion above passes under any zone, so neither conversion is
guarded unless the VALUE is checked against a deliberately non-UTC process.
"""

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from loguru import logger

from core.log_stream import CLIENT_QUEUE_MAX, LogBroadcastHub, hub
from core.log_stream import _TIMESTAMP_FORMAT as STAMP_FORMAT

# +05:30: a non-whole-hour offset, so any unnoticed local/UTC mixup is unmistakable.
HOSTILE_TZ = "Asia/Kolkata"


@pytest.fixture
def hostile_tz(monkeypatch):
    """Run the test body with the process in a zone that is not UTC, then put it back.

    teardown undoes monkeypatch (restoring TZ) before tzset(), and re-runs tzset so the
    interpreter's cached zone matches the restored env var — otherwise every later test
    would inherit this zone. The assertion is the point: if tzset ever stopped working,
    the UTC checks below would pass vacuously.
    """
    if not hasattr(time, "tzset"):
        pytest.skip("requires POSIX time.tzset()")

    monkeypatch.setenv("TZ", HOSTILE_TZ)
    time.tzset()

    offset = datetime.now().astimezone().utcoffset()
    assert offset == timedelta(hours=5, minutes=30), (
        f"hostile TZ did not take effect (local offset {offset}); "
        "the UTC assertions in this test would be vacuous"
    )

    yield HOSTILE_TZ

    monkeypatch.undo()
    time.tzset()


def _parse_stamp(text: str) -> datetime:
    """The offset-less stamps the Logs page speaks, read back as UTC."""
    return datetime.strptime(text, STAMP_FORMAT).replace(tzinfo=timezone.utc)


def _assert_is_utc_now(published: datetime, what: str) -> None:
    """Assert the stamp is the current UTC wall clock, not merely a parseable string.

    ±1s absorbs the second-boundary race between the log record and this call.
    """
    drift = (published - datetime.now(timezone.utc)).total_seconds()
    assert abs(drift) <= 1, f"{what} is {published}, {drift:+.0f}s from UTC now — it is not UTC"


def test_publish_fans_out_to_all_subscribers():
    async def scenario():
        h = LogBroadcastHub()
        q1, q2 = h.subscribe(), h.subscribe()
        h.publish({"message": "one"})
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run
        return q1.get_nowait(), q2.get_nowait()

    a, b = asyncio.run(scenario())
    assert a == {"message": "one"} and b == {"message": "one"}


def test_unsubscribed_client_stops_receiving():
    async def scenario():
        h = LogBroadcastHub()
        q = h.subscribe()
        h.unsubscribe(q)
        h.publish({"message": "gone"})
        await asyncio.sleep(0)
        return q.qsize()

    assert asyncio.run(scenario()) == 0


def test_stalled_client_drops_oldest_not_newest():
    async def scenario():
        h = LogBroadcastHub()
        q = h.subscribe()
        for i in range(CLIENT_QUEUE_MAX + 5):
            h.publish({"n": i})
        await asyncio.sleep(0)
        drained = []
        while not q.empty():
            drained.append(q.get_nowait()["n"])
        return drained

    drained = asyncio.run(scenario())
    assert len(drained) == CLIENT_QUEUE_MAX
    assert drained[-1] == CLIENT_QUEUE_MAX + 4, "newest entry must survive"
    assert drained[0] == 5, "oldest entries are the ones dropped"


def test_publish_without_loop_or_subscribers_is_a_noop():
    h = LogBroadcastHub()
    h.publish({"message": "nobody home"})  # must not raise — logging can't depend on viewers


def test_sink_emits_page_frame_shape_and_skips_blanks():
    async def scenario():
        # setup_logging (run at app import) registers broadcast_sink globally — use it,
        # adding it again here would double-publish every record.
        q = hub.subscribe()
        logger.bind(log_structural=True).opt(raw=True).info("\n")  # file-only spacer
        logger.info("[  TEST   ] • hello stream")
        await asyncio.sleep(0)
        entries = []
        while not q.empty():
            entries.append(q.get_nowait())
        hub.unsubscribe(q)
        return entries

    entries = asyncio.run(scenario())
    assert len(entries) == 1, "the raw blank spacer must not reach the stream"
    entry = entries[0]
    assert entry["message"] == "[  TEST   ] • hello stream"
    assert entry["level"] == "INFO"
    # Matches the file format the page's backlog parser expects: YY/MM/DD HH:MM:SS
    assert len(entry["timestamp"]) == 17 and entry["timestamp"][2] == "/"


def test_stream_timestamp_is_utc_not_host_local(hostile_tz):
    """broadcast_sink is registered with format="{message}" (core/logging.py), so loguru's
    !UTC never applies to it — the sink has to convert record["time"] itself.

    Dropping that .astimezone() would leave the page on host-local time while the log files
    are UTC: one job, two clocks, depending on where you read it.
    """
    async def scenario():
        q = hub.subscribe()
        logger.info("[  TEST   ] • utc stream probe")
        await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run
        entries = []
        while not q.empty():
            entries.append(q.get_nowait())
        hub.unsubscribe(q)
        return entries

    entries = asyncio.run(scenario())
    assert len(entries) == 1
    _assert_is_utc_now(_parse_stamp(entries[0]["timestamp"]), f"stream timestamp {entries[0]['timestamp']!r}")


def test_file_sink_renders_utc_under_a_hostile_tz(hostile_tz):
    """The !UTC in LOGURU_TIMESTAMP_FORMAT is what pins all three file/console sinks.

    It is a formatter modifier, so it must also leave the rendered shape byte-identical —
    a 17-char stamp with no offset or Z, since api/jobs.py re-parses these lines.
    """
    from core.logging import LOGURU_TIMESTAMP_FORMAT

    lines = []
    sink_id = logger.add(
        lines.append,
        format=f"{{time:{LOGURU_TIMESTAMP_FORMAT}}} | {{level: <7}} | {{message}}",
    )
    try:
        logger.info("utc file sink probe")
    finally:
        logger.remove(sink_id)

    stamped = [line for line in lines if "utc file sink probe" in line]
    assert len(stamped) == 1, f"probe line not rendered into the sink: {lines!r}"
    stamp = str(stamped[0]).split(" | ")[0]

    assert len(stamp) == 17, f"rendered shape changed: {stamp!r}"
    _assert_is_utc_now(_parse_stamp(stamp), f"file-sink timestamp {stamp!r}")

"""The live-log broadcast hub: loguru sink → per-client queues → WS batch frames.

These pin the push pipeline that replaced file tailing: entries fan out to every
subscriber, a stalled client drops its oldest lines instead of blocking logging,
and the sink emits the exact frame shape the Logs page speaks (skipping the raw
blank spacers that exist only for file readability).
"""

import asyncio

from loguru import logger

from core.log_stream import CLIENT_QUEUE_MAX, LogBroadcastHub, hub


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

"""The SSE pub/sub core, tested directly (no HTTP: the stream generator blocks by design)."""
from app.core.events import Broadcaster, stream


def test_publish_reaches_subscribers_for_the_right_branch():
    bus = Broadcaster()
    sub_id, q = bus.subscribe()
    bus.publish(branch_id=5, event="update")
    assert q.get_nowait() == (5, "update")
    bus.unsubscribe(sub_id)


def test_stream_yields_only_matching_branch_events():
    """stream() subscribes internally, so events must be published after the generator starts
    (via a background thread) rather than pre-queued against a separately-obtained subscription."""
    import threading
    import time

    bus = Broadcaster()
    gen = stream(bus, branch_id=5, heartbeat_seconds=5)

    def publish_soon():
        time.sleep(0.05)
        bus.publish(branch_id=1, event="update")  # wrong branch: must be skipped, not yielded
        bus.publish(branch_id=5, event="update")

    threading.Thread(target=publish_soon, daemon=True).start()
    next(gen)  # the initial ": connected" comment
    chunk = next(gen)
    assert chunk.startswith("event: update\n")
    gen.close()


def test_stream_sends_something_immediately_on_connect():
    """Regression: the first yield must not wait for heartbeat_seconds, or the client (and the
    HTTP response's headers, on servers that don't flush headers until the first body byte) sits
    with no confirmation the connection succeeded for the full heartbeat interval."""
    bus = Broadcaster()
    gen = stream(bus, branch_id=1, heartbeat_seconds=999)
    chunk = next(gen)
    assert chunk == ": connected\n\n"
    gen.close()


def test_stream_heartbeats_when_idle():
    bus = Broadcaster()
    gen = stream(bus, branch_id=1, heartbeat_seconds=0)
    next(gen)  # the initial ": connected" comment
    chunk = next(gen)
    assert chunk.startswith(": keep-alive")
    gen.close()


def test_unsubscribe_removes_the_queue():
    bus = Broadcaster()
    sub_id, _ = bus.subscribe()
    assert sub_id in bus._subscribers
    bus.unsubscribe(sub_id)
    assert sub_id not in bus._subscribers


def test_full_queue_does_not_raise():
    bus = Broadcaster()
    _, q = bus.subscribe()
    for _ in range(150):  # over the maxsize=100 cap
        bus.publish(branch_id=1)
    assert q.qsize() <= 100  # publish() never raises even when a slow subscriber falls behind

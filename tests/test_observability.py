import sys, pytest, time, asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from observability.logger import Logger, LogLevel, log
from observability.tracer import Tracer, Span, span_ctx, tracer as global_tracer
from observability.tracer import Counter, Gauge, Histogram, MetricsRegistry, metrics
from observability.tracer import EventBus, event_bus


class TestLogger:
    def test_get_logger(self):
        l = Logger.get("test_logger")
        assert l.name == "test_logger"

    def test_singleton(self):
        a = Logger.get("test_single")
        b = Logger.get("test_single")
        assert a is b

    def test_log_levels(self):
        assert LogLevel.DEBUG < LogLevel.INFO
        assert LogLevel.ERROR > LogLevel.WARN

    def test_log_no_crash(self):
        log.info("Test message", key="value")


class TestTracer:
    def test_start_finish_span(self):
        t = Tracer()
        s = t.start_span("test_op")
        assert s.name == "test_op"
        assert s.status == "running"
        t.finish_span(s, "ok")
        assert s.status == "ok"
        assert s.duration_ms > 0

    def test_nested_spans(self):
        t = Tracer()
        root = t.start_span("root")
        child = t.start_span("child", parent_span=root)
        assert child.parent_id == root.span_id
        assert child.trace_id == root.trace_id
        t.finish_span(child)
        t.finish_span(root)

    def test_span_attributes(self):
        t = Tracer()
        s = t.start_span("attr_test", model="gpt-4o", tokens=100)
        assert s.attributes.get("model") == "gpt-4o"
        t.finish_span(s)

    def test_span_events(self):
        t = Tracer()
        s = t.start_span("event_test")
        s.add_event("step1", detail="analyzing")
        assert len(s.events) == 1
        t.finish_span(s)

    def test_error_span(self):
        t = Tracer()
        s = t.start_span("error_test")
        t.finish_span(s, "error", "something went wrong")
        assert s.status == "error"
        assert "wrong" in s.error

    def test_span_to_dict(self):
        t = Tracer()
        s = t.start_span("serialize")
        t.finish_span(s)
        d = s.to_dict()
        assert d["name"] == "serialize"
        assert "duration_ms" in d

    def test_recent_spans(self):
        t = Tracer()
        for i in range(5):
            s = t.start_span(f"span_{i}")
            t.finish_span(s)
        assert len(t.recent_spans(5)) == 5

    def test_tracer_stats(self):
        t = Tracer()
        s = t.start_span("stats_test")
        t.finish_span(s)
        stats = t.stats()
        assert stats["total_spans"] >= 1

    def test_global_tracer(self):
        s = global_tracer.start_span("global_test")
        global_tracer.finish_span(s, "ok")


class TestMetrics:
    def test_counter(self):
        c = Counter("test_counter")
        assert c.value == 0
        c.inc()
        c.inc(5)
        assert c.value == 6

    def test_gauge(self):
        g = Gauge("test_gauge")
        g.set(10)
        assert g.value == 10
        g.inc(5)
        g.dec(2)
        assert g.value == 13

    def test_histogram(self):
        h = Histogram("test_histogram")
        h.record(10)
        h.record(20)
        h.record(100)
        h.record(200)
        assert h._count == 4
        # p50 should be around 25 (median of first two values in bucket 25)
        assert 20 <= h.p50 <= 100

    def test_metrics_registry(self):
        registry = MetricsRegistry()
        c = registry.counter("my_counter")
        c.inc(3)
        snap = registry.snapshot()
        assert "counters" in snap
        assert "my_counter" in snap["counters"]
        assert snap["counters"]["my_counter"]["value"] == 3

    def test_predefined_metrics(self):
        from observability.tracer import llm_requests, tool_calls, active_sessions
        llm_requests.inc()
        tool_calls.inc(2)
        active_sessions.set(5)
        assert llm_requests.value == 1
        assert tool_calls.value == 2
        assert active_sessions.value == 5


class TestEventBus:
    def test_emit_and_collect(self):
        events = []
        def handler(e):
            events.append(e)
        eb = EventBus()
        eb.subscribe("test.event", handler)
        eb.emit_sync("test.event", data="hello")
        assert len(events) == 1
        assert events[0]["data"] == "hello"

    def test_wildcard_handler(self):
        events = []
        def catch_all(e):
            events.append(e)
        eb = EventBus()
        eb.subscribe("*", catch_all)
        eb.emit_sync("any.event", value=42)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_async_emit(self):
        events = []
        async def handler(e):
            events.append(e)
        eb = EventBus()
        eb.subscribe("async.test", handler)
        await eb.emit("async.test", key="val")
        assert len(events) == 1

    def test_recent_events(self):
        eb = EventBus()
        eb.emit_sync("history.test", a=1, b=2)
        recent = eb.recent_events(10)
        assert any(e["type"] == "history.test" for e in recent)
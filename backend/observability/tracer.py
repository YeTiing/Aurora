# ═══════════════════════════════════════════════════════════════
# Span Tracing
# ═══════════════════════════════════════════════════════════════
import time, threading, uuid, asyncio
from dataclasses import dataclass, field
from typing import Any

@dataclass
class Span:
    name: str
    span_id: str = ""
    parent_id: str = ""
    trace_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    attributes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    status: str = "running"
    error: str = ""

    def __post_init__(self):
        if not self.span_id:
            import uuid
            self.span_id = uuid.uuid4().hex[:12]
        if not self.start_time:
            self.start_time = time.time()

    @property
    def duration_ms(self) -> float:
        end = self.end_time or time.time()
        return (end - self.start_time) * 1000

    def add_event(self, name: str, **attrs):
        self.events.append({"name": name, "timestamp": time.time(), **attrs})

    def set_attribute(self, key: str, value: Any):
        self.attributes[key] = value

    def finish(self, status: str = "ok", error: str = ""):
        self.end_time = time.time()
        self.status = status
        self.error = error

    def to_dict(self) -> dict:
        return {
            "span_id": self.span_id, "parent_id": self.parent_id,
            "trace_id": self.trace_id, "name": self.name,
            "start_time": self.start_time, "end_time": self.end_time,
            "duration_ms": self.duration_ms, "status": self.status,
            "error": self.error, "attributes": self.attributes,
            "events": self.events,
        }


class Tracer:
    def __init__(self):
        self._spans: dict[str, Span] = {}
        self._active_stack: dict[str, list[Span]] = {}
        self._completed: list[Span] = []
        self._lock = threading.Lock()
        self._max_completed = 1000

    def start_span(self, name: str, parent_span: Span | None = None, **attrs) -> Span:
        trace_id = ""
        parent_id = ""
        if parent_span:
            trace_id = parent_span.trace_id or uuid.uuid4().hex[:16]
            parent_id = parent_span.span_id
        else:
            trace_id = uuid.uuid4().hex[:16]
        span = Span(name=name, parent_id=parent_id, trace_id=trace_id)
        for k, v in attrs.items():
            span.set_attribute(k, v)
        with self._lock:
            self._spans[span.span_id] = span
            if trace_id not in self._active_stack:
                self._active_stack[trace_id] = []
            self._active_stack[trace_id].append(span)
        return span

    def finish_span(self, span: Span, status: str = "ok", error: str = ""):
        with self._lock:
            span.finish(status, error)
            if span.trace_id in self._active_stack:
                if span in self._active_stack[span.trace_id]:
                    self._active_stack[span.trace_id].remove(span)
            self._completed.append(span)
            if len(self._completed) > self._max_completed:
                self._completed = self._completed[-self._max_completed:]

    def current_span(self, trace_id: str = "") -> Span | None:
        with self._lock:
            if trace_id and trace_id in self._active_stack:
                stack = self._active_stack[trace_id]
                return stack[-1] if stack else None
            active = sorted(self._spans.values(), key=lambda s: s.start_time, reverse=True)
            for s in active:
                if s.end_time == 0:
                    return s
        return None

    def get_trace(self, trace_id: str) -> list[Span]:
        return [s for s in self._spans.values() if s.trace_id == trace_id]

    def recent_spans(self, limit: int = 50) -> list[Span]:
        return self._completed[-limit:]

    def stats(self) -> dict:
        spans = self._completed[-100:]
        if not spans:
            return {"total_spans": len(self._completed), "avg_duration_ms": 0}
        avg = sum(s.duration_ms for s in spans) / len(spans)
        errors = sum(1 for s in spans if s.status == "error")
        return {
            "total_spans": len(self._completed),
            "recent_spans": len(spans),
            "avg_duration_ms": round(avg, 1),
            "error_rate": f"{errors}/{len(spans)}",
        }


tracer = Tracer()


class span_ctx:
    def __init__(self, name: str, parent: Span | None = None, tracer_instance: Tracer | None = None, **attrs):
        self._name = name
        self._parent = parent
        self._tracer = tracer_instance or tracer
        self._span: Span | None = None
        self._attrs = attrs

    async def __aenter__(self) -> Span:
        self._span = self._tracer.start_span(self._name, parent_span=self._parent, **self._attrs)
        return self._span

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._span:
            if exc_type:
                self._tracer.finish_span(self._span, "error", str(exc_val)[:500])
            else:
                self._tracer.finish_span(self._span, "ok")

    def __enter__(self):
        self._span = self._tracer.start_span(self._name, parent_span=self._parent, **self._attrs)
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._span:
            if exc_type:
                self._tracer.finish_span(self._span, "error", str(exc_val)[:500])
            else:
                self._tracer.finish_span(self._span, "ok")


# ═══════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════
from collections import defaultdict

class Counter:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, delta: int = 1) -> int:
        with self._lock:
            self._value += delta
            return self._value

    @property
    def value(self) -> int:
        return self._value

    def snapshot(self) -> dict:
        return {"name": self.name, "type": "counter", "value": self._value}


class Gauge:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._value: float = 0.0
        self._lock = threading.Lock()

    def set(self, value: float):
        with self._lock:
            self._value = value

    def inc(self, delta: float = 1.0):
        with self._lock:
            self._value += delta

    def dec(self, delta: float = 1.0):
        with self._lock:
            self._value -= delta

    @property
    def value(self) -> float:
        return self._value

    def snapshot(self) -> dict:
        return {"name": self.name, "type": "gauge", "value": self._value}


class Histogram:
    BUCKETS = [1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000]

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self._buckets: dict[int, int] = defaultdict(int)
        self._sum = 0.0
        self._count = 0
        self._lock = threading.Lock()

    def record(self, value: float):
        with self._lock:
            self._count += 1
            self._sum += value
            for b in self.BUCKETS:
                if value <= b:
                    self._buckets[b] += 1
                    break
            else:
                self._buckets[float("inf")] = self._buckets.get(float("inf"), 0) + 1

    @property
    def avg(self) -> float:
        return self._sum / max(self._count, 1)

    @property
    def p50(self) -> float:
        return self._percentile(0.50)

    @property
    def p95(self) -> float:
        return self._percentile(0.95)

    @property
    def p99(self) -> float:
        return self._percentile(0.99)

    def _percentile(self, pct: float) -> float:
        target = pct * self._count
        cumulative = 0
        for b in sorted(self._buckets.keys()):
            cumulative += self._buckets[b]
            if cumulative >= target:
                return float(b)
        return 0.0

    def snapshot(self) -> dict:
        return {
            "name": self.name, "type": "histogram",
            "count": self._count, "sum": self._sum,
            "avg": round(self.avg, 1), "p50": self.p50,
            "p95": self.p95, "p99": self.p99,
        }


class MetricsRegistry:
    def __init__(self):
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, description: str = "") -> Counter:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = Counter(name, description)
            return self._counters[name]

    def gauge(self, name: str, description: str = "") -> Gauge:
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = Gauge(name, description)
            return self._gauges[name]

    def histogram(self, name: str, description: str = "") -> Histogram:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = Histogram(name, description)
            return self._histograms[name]

    def snapshot(self) -> dict:
        return {
            "counters": {n: c.snapshot() for n, c in self._counters.items()},
            "gauges": {n: g.snapshot() for n, g in self._gauges.items()},
            "histograms": {n: h.snapshot() for n, h in self._histograms.items()},
        }


metrics = MetricsRegistry()

llm_requests = metrics.counter("llm_requests_total", "Total LLM API requests")
llm_tokens = metrics.counter("llm_tokens_total", "Total tokens consumed")
llm_latency = metrics.histogram("llm_latency_ms", "LLM call latency in ms")
tool_calls = metrics.counter("tool_calls_total", "Total tool invocations")
tool_errors = metrics.counter("tool_errors_total", "Failed tool calls")
agent_turns = metrics.counter("agent_turns_total", "Agent turn iterations")
active_sessions = metrics.gauge("active_sessions", "Currently active sessions")
task_completions = metrics.counter("task_completions_total", "Completed tasks")
task_failures = metrics.counter("task_failures_total", "Failed tasks")


# ═══════════════════════════════════════════════════════════════
# Event Bus
# ═══════════════════════════════════════════════════════════════

class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list] = defaultdict(list)
        self._lock = threading.Lock()
        self._event_history: list[dict] = []
        self._max_history = 500

    def subscribe(self, event_type: str, callback):
        with self._lock:
            self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback):
        with self._lock:
            if callback in self._subscribers[event_type]:
                self._subscribers[event_type].remove(callback)

    async def emit(self, event_type: str, **data):
        event = {"type": event_type, "timestamp": time.time(), **data}
        with self._lock:
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]
        callbacks = self._subscribers.get(event_type, []) + self._subscribers.get("*", [])
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event)
                else:
                    cb(event)
            except Exception:
                pass

    def emit_sync(self, event_type: str, **data):
        event = {"type": event_type, "timestamp": time.time(), **data}
        with self._lock:
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]
        for cb in self._subscribers.get(event_type, []) + self._subscribers.get("*", []):
            try:
                cb(event)
            except Exception:
                pass

    def recent_events(self, limit: int = 100) -> list[dict]:
        return self._event_history[-limit:]


event_bus = EventBus()
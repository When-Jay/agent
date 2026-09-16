"""Langfuse subscriber tests (runtime-dispatch-spec.md section 7).

Verifies optional enablement, event->trace mapping and that Langfuse
failures never propagate into runtime execution.
"""

from uuid import UUID, uuid4

from agent_platform.config import Settings
from agent_platform.observability import LangfuseEventSubscriber, attach_langfuse_subscriber
from agent_platform.runtime.core import EventBus, InMemoryRuntimeStore, RuntimeEventType


class _FakeObservation:
    def __init__(self, recorder, **kwargs):
        self._recorder = recorder
        self.kwargs = kwargs

    def start_observation(self, **kwargs):
        child = _FakeObservation(self._recorder, **kwargs)
        self._recorder.append(("start", child.kwargs))
        return child

    def update(self, **kwargs):
        self._recorder.append(("update", kwargs))
        return self

    def end(self):
        self._recorder.append(("end", {}))


class _FakeLangfuseClient:
    def __init__(self):
        self.recorder: list = []
        self.flushed = False

    def start_observation(self, **kwargs):
        trace = _FakeObservation(self.recorder, **kwargs)
        self.recorder.append(("trace", kwargs, trace))
        return trace

    def flush(self):
        self.flushed = True


def _publish(bus, event_type, payload):
    bus.publish(run_id=str(uuid4()), event_type=event_type, payload=payload)


def _unconfigured_settings():
    return Settings(langfuse_public_key="", langfuse_secret_key="")


def test_disabled_without_configuration():
    bus = EventBus(InMemoryRuntimeStore())
    subscriber = attach_langfuse_subscriber(bus, settings=_unconfigured_settings())

    assert isinstance(subscriber, LangfuseEventSubscriber)
    assert subscriber.enabled is False
    # Publishing must work untouched while disabled.
    _publish(bus, RuntimeEventType.RUN_STARTED, {})


def test_events_map_to_trace_generations_and_tool_spans():
    client = _FakeLangfuseClient()
    bus = EventBus(InMemoryRuntimeStore())
    subscriber = attach_langfuse_subscriber(bus, settings=_unconfigured_settings(), client=client)
    assert subscriber.enabled is True

    run_id = str(uuid4())
    bus.publish(run_id=run_id, event_type=RuntimeEventType.RUN_STARTED, payload={"runtime_type": "agent"})
    bus.publish(run_id=run_id, event_type=RuntimeEventType.LLM_STARTED, payload={"model": "fake-model"})
    bus.publish(
        run_id=run_id,
        event_type=RuntimeEventType.LLM_COMPLETED,
        payload={"input_tokens": 3, "output_tokens": 5},
    )
    bus.publish(run_id=run_id, event_type=RuntimeEventType.TOOL_CALL_COMPLETED, payload={"tool": "add"})
    bus.publish(run_id=run_id, event_type=RuntimeEventType.RUN_FAILED, payload={"error": "boom"})

    traces = [entry for entry in client.recorder if entry[0] == "trace"]
    assert len(traces) == 1
    trace_kwargs, trace = traces[0][1], traces[0][2]
    assert trace_kwargs["name"] == "runtime-run"
    assert trace_kwargs["trace_context"]["trace_id"] == UUID(run_id).hex

    kinds = [entry[0] for entry in client.recorder if entry[0] != "trace"]
    # generation started+ended, tool span started+ended, failed-run update, trace end.
    assert kinds == ["start", "end", "start", "end", "update", "end"]
    generation_kwargs = client.recorder[1][1]
    assert generation_kwargs["as_type"] == "generation"
    assert generation_kwargs["model"] == "fake-model"
    assert generation_kwargs["usage_details"] == {"input": 3, "output": 5}
    tool_kwargs = client.recorder[3][1]
    assert tool_kwargs["name"] == "tool:add"
    failed_update = client.recorder[5][1]
    assert failed_update["level"] == "ERROR"
    assert client.flushed is True


def test_client_failures_never_break_event_flow():
    class _ExplodingClient:
        def start_observation(self, **kwargs):  # noqa: ANN003
            raise RuntimeError("langfuse down")

        def flush(self):
            raise RuntimeError("langfuse down")

    bus = EventBus(InMemoryRuntimeStore())
    attach_langfuse_subscriber(bus, settings=_unconfigured_settings(), client=_ExplodingClient())

    # Neither publishing nor run completion may raise.
    _publish(bus, RuntimeEventType.RUN_STARTED, {})
    _publish(bus, RuntimeEventType.RUN_COMPLETED, {"output": {}})

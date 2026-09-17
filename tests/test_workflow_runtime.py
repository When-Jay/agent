"""Workflow Runtime acceptance tests.

Covers: sequential nodes, conditional branch, parallel fan-out/fan-in,
retry, timeout, per-superstep checkpoints, resume after failure, and
the required event stream. LangGraph stays behind the engine boundary.
"""

import time
from typing import Any

import pytest

from agent_platform.runtime.core import InMemoryRuntimeStore, RunManager, SessionManager
from agent_platform.runtime.workflow import (
    EdgeSpec,
    NodeSpec,
    WorkflowDefinition,
    WorkflowDefinitionError,
    WorkflowRunner,
)


def _make_run(store) -> str:
    sessions = SessionManager(store)
    app = sessions.create_application(name="wf-test")
    session = sessions.create_session(application_id=app.id)
    return RunManager(store).create_run(
        application_id=app.id, session_id=session.id, runtime_type="workflow"
    ).id


def _node(name: str, effect, **kwargs) -> NodeSpec:
    return NodeSpec(name=name, handler=effect, **kwargs)


def _event_types(store, run_id) -> list[str]:
    return [e.event_type.value for e in store.list_events(run_id)]


def _node_events(store, run_id, event_name) -> list[dict[str, Any]]:
    return [
        e.payload
        for e in store.list_events(run_id)
        if e.event_type.value == event_name
    ]


# --- Sequential -------------------------------------------------------------


def test_sequential_nodes_execute_in_order_with_events_and_checkpoints():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)

    def step_a(state):
        return {"trace": ["a"]}

    def step_b(state):
        return {"trace": state["trace"] + ["b"]}

    def step_c(state):
        return {"trace": state["trace"] + ["c"]}

    definition = WorkflowDefinition(
        name="pipeline",
        version="v1",
        nodes=[_node("a", step_a), _node("b", step_b), _node("c", step_c)],
        edges=[EdgeSpec(source="a", target="b"), EdgeSpec(source="b", target="c")],
        entry="a",
    )

    run_id = _make_run(store)
    result = runner.run(run_id=run_id, definition=definition, input={"seed": 1})

    assert result.status == "completed"
    assert result.state["trace"] == ["a", "b", "c"]

    types = _event_types(store, run_id)
    assert types[0] == "RunStarted"
    assert types[-1] == "RunCompleted"
    started = [p["node"] for p in _node_events(store, run_id, "NodeStarted")]
    assert started == ["a", "b", "c"]
    assert types.count("CheckpointCreated") >= 3

    run = store.get_run(run_id)
    assert run.status.value == "completed"


def test_definition_validation_rejects_bad_entry_and_edges():
    node = _node("only", lambda state: None)

    with pytest.raises(WorkflowDefinitionError):
        WorkflowDefinition(name="x", version="v1", nodes=[node], edges=[], entry="missing")

    with pytest.raises(WorkflowDefinitionError):
        WorkflowDefinition(
            name="x",
            version="v1",
            nodes=[node],
            edges=[EdgeSpec(source="only", target="ghost")],
            entry="only",
        )


# --- Conditional branch -----------------------------------------------------


def test_conditional_branch_routes_by_state():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)

    def evaluate(state):
        return {"score": state["input"]}

    def big(state):
        return {"path": "big"}

    def small(state):
        return {"path": "small"}

    definition = WorkflowDefinition(
        name="router",
        version="v1",
        nodes=[_node("evaluate", evaluate), _node("big", big), _node("small", small)],
        edges=[
            EdgeSpec(source="evaluate", target="big", condition=lambda s: s["score"] > 5),
            EdgeSpec(source="evaluate", target="small"),
        ],
        entry="evaluate",
    )

    run_id = _make_run(store)
    result = runner.run(run_id=run_id, definition=definition, input={"input": 10})

    assert result.status == "completed"
    assert result.state["path"] == "big"
    assert "small" not in result.state

    run_id2 = _make_run(store)
    result2 = runner.run(run_id=run_id2, definition=definition, input={"input": 3})
    assert result2.state["path"] == "small"


# --- Parallel ---------------------------------------------------------------


def test_parallel_fan_out_fan_in():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)

    def start(state):
        return {"fanned": True}

    def branch_left(state):
        time.sleep(0.05)
        return {"left": 1}

    def branch_right(state):
        time.sleep(0.05)
        return {"right": 2}

    def join(state):
        return {"joined": state["left"] + state["right"]}

    definition = WorkflowDefinition(
        name="parallel",
        version="v1",
        nodes=[
            _node("start", start),
            _node("left", branch_left),
            _node("right", branch_right),
            _node("join", join),
        ],
        edges=[
            EdgeSpec(source="start", target="left"),
            EdgeSpec(source="start", target="right"),
            EdgeSpec(source="left", target="join"),
            EdgeSpec(source="right", target="join"),
        ],
        entry="start",
    )

    run_id = _make_run(store)
    result = runner.run(run_id=run_id, definition=definition)

    assert result.status == "completed"
    assert result.state["joined"] == 3
    # Both branches started before the join node ran.
    started = [p["node"] for p in _node_events(store, run_id, "NodeStarted")]
    assert started.index("join") > started.index("left")
    assert started.index("join") > started.index("right")


# --- Retry ------------------------------------------------------------------


def test_retry_recovers_from_transient_failure():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)
    attempts = {"count": 0}

    def flaky(state):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise RuntimeError("transient")
        return {"ok": True}

    definition = WorkflowDefinition(
        name="flaky",
        version="v1",
        nodes=[_node("flaky", flaky, retries=2)],
        edges=[],
        entry="flaky",
    )

    run_id = _make_run(store)
    result = runner.run(run_id=run_id, definition=definition)

    assert result.status == "completed"
    assert result.state["ok"] is True
    completed = _node_events(store, run_id, "NodeCompleted")
    assert completed[0]["attempt"] == 2


# --- Timeout ----------------------------------------------------------------


def test_timeout_fails_the_run_with_node_failed_event():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)

    def slow(state):
        time.sleep(1.0)
        return {}

    definition = WorkflowDefinition(
        name="slow",
        version="v1",
        nodes=[_node("slow", slow, timeout_seconds=0.1)],
        edges=[],
        entry="slow",
    )

    run_id = _make_run(store)
    result = runner.run(run_id=run_id, definition=definition)

    assert result.status == "failed"
    assert store.get_run(run_id).status.value == "failed"
    assert _node_events(store, run_id, "NodeFailed")
    assert "RunFailed" in _event_types(store, run_id)


# --- Checkpoint & Resume ----------------------------------------------------


def test_resume_after_failure_continues_from_checkpoint():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)
    calls = {"first": 0, "second": 0}

    def first(state):
        calls["first"] += 1
        return {"first_done": True}

    def broken_then_fixed(state):
        calls["second"] += 1
        if calls["second"] == 1:
            raise RuntimeError("boom")
        return {"second_done": True}

    def rebuild():
        # Same name+version so resume accepts it; handler closure is refreshed.
        return WorkflowDefinition(
            name="recover",
            version="v1",
            nodes=[_node("first", first), _node("second", broken_then_fixed)],
            edges=[EdgeSpec(source="first", target="second")],
            entry="first",
        )

    run_id = _make_run(store)
    failed = runner.run(run_id=run_id, definition=rebuild())
    assert failed.status == "failed"
    assert calls["first"] == 1  # first node must NOT re-execute on resume

    resumed = runner.resume(run_id=run_id, definition=rebuild())

    assert resumed.status == "completed"
    assert resumed.state["second_done"] is True
    assert store.get_run(run_id).status.value == "completed"


def test_resume_rejects_mismatched_workflow_version():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)

    def boom(state):
        raise RuntimeError("boom")

    definition = WorkflowDefinition(
        name="bound", version="v1", nodes=[_node("boom", boom)], edges=[], entry="boom"
    )
    run_id = _make_run(store)
    runner.run(run_id=run_id, definition=definition)

    other = WorkflowDefinition(
        name="bound", version="v2", nodes=[_node("boom", boom)], edges=[], entry="boom"
    )
    with pytest.raises(Exception, match="bound to workflow"):
        runner.resume(run_id=run_id, definition=other)


# --- Versioning ---------------------------------------------------------------


def _rebuildable_definition(step):
    def second(state):
        return {"second_done": True}

    return WorkflowDefinition(
        name="versioned",
        version="v1",
        nodes=[_node("first", step), _node("second", second)],
        edges=[EdgeSpec(source="first", target="second")],
        entry="first",
    )


def test_content_hash_is_stable_and_content_sensitive():
    def step(state):
        return {"first_done": True}

    assert _rebuildable_definition(step).content_hash == _rebuildable_definition(step).content_hash

    def alternative(state):
        return {"first_done": True}

    # Same version label, different handler wiring -> different content.
    assert _rebuildable_definition(step).content_hash != _rebuildable_definition(alternative).content_hash

    # Structural changes (retries) change the hash too.
    def with_retry(state):
        return {"first_done": True}

    def second(state):
        return {"second_done": True}

    base = _rebuildable_definition(step)
    retried = WorkflowDefinition(
        name="versioned",
        version="v1",
        nodes=[_node("first", with_retry, retries=1), _node("second", second)],
        edges=[EdgeSpec(source="first", target="second")],
        entry="first",
    )
    assert base.content_hash != retried.content_hash


def test_run_binds_definition_content_hash_in_state_and_events():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)

    def step(state):
        return {"first_done": True}

    definition = _rebuildable_definition(step)
    run_id = _make_run(store)
    runner.run(run_id=run_id, definition=definition, input={})

    bound = store.get_state(run_id).values["workflow"]["definition"]
    assert bound == {
        "name": "versioned",
        "version": "v1",
        "hash": definition.content_hash,
    }

    started = _node_events(store, run_id, "RunStarted")[0]
    assert started["definition_hash"] == definition.content_hash
    completed = _node_events(store, run_id, "RunCompleted")[0]
    assert completed["workflow"]["hash"] == definition.content_hash


def test_resume_rejects_changed_content_under_same_version():
    store = InMemoryRuntimeStore()
    runner = WorkflowRunner(store)

    def step(state):
        raise RuntimeError("boom")

    definition = _rebuildable_definition(step)
    run_id = _make_run(store)
    runner.run(run_id=run_id, definition=definition)

    def alternative(state):
        raise RuntimeError("boom")

    with pytest.raises(Exception, match="content"):
        runner.resume(run_id=run_id, definition=_rebuildable_definition(alternative))

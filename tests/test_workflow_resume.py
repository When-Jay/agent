"""Cross-process workflow resume: durable engine checkpoints.

The engine's default per-run MemorySaver only resumes within one
process. With StoreCheckpointSaver over a shared database, a fresh
WorkflowRunner reattaches to the interrupted or failed LangGraph thread:
HITL pauses and failure recovery survive instance restarts, and a retry
(re-dispatch) starts from a clean thread (workflow-runtime-spec.md
sections 3 and 6; runtime-spec.md section 9, shared Checkpoint concept).
"""

from sqlalchemy import create_engine

from agent_platform.runtime.checkpointing import StoreCheckpointSaver
from agent_platform.runtime.core import InMemoryRuntimeStore, RunManager, RunStatus
from agent_platform.runtime.workflow import (
    EdgeSpec,
    LangGraphWorkflowEngine,
    NodeSpec,
    WorkflowDefinition,
    WorkflowRegistry,
    WorkflowRunner,
)


def _approval_workflow() -> WorkflowDefinition:
    def build_request(state):
        return {"question": "approve transfer?", "amount": state.get("amount")}

    def done(state):
        return {"done": True}

    return WorkflowDefinition(
        name="approval",
        version="1",
        nodes=[
            NodeSpec(name="approve", handler=build_request, node_type="human"),
            NodeSpec(name="done", handler=done),
        ],
        edges=[EdgeSpec(source="approve", target="done")],
        entry="approve",
    )


def _flaky_workflow(calls: dict) -> WorkflowDefinition:
    def flaky(state):
        calls["flaky"] = calls.get("flaky", 0) + 1
        if calls["flaky"] == 1:
            raise RuntimeError("transient boom")
        return {"recovered": True}

    def done(state):
        return {"done": True}

    return WorkflowDefinition(
        name="flaky",
        version="1",
        nodes=[
            NodeSpec(name="flaky", handler=flaky),
            NodeSpec(name="done", handler=done),
        ],
        edges=[EdgeSpec(source="flaky", target="done")],
        entry="flaky",
    )


def _runner(store, database_path, definition: WorkflowDefinition) -> WorkflowRunner:
    """A runner whose engine checkpoints durably, as dispatch composes it."""
    registry = WorkflowRegistry()
    registry.register(definition)
    saver = StoreCheckpointSaver(create_engine(f"sqlite:///{database_path}"))
    return WorkflowRunner(store, engine=LangGraphWorkflowEngine(saver), registry=registry)


def _seed_run(store):
    return RunManager(store).create_run(
        application_id="app-1", session_id="s-1", runtime_type="workflow", input={"amount": 100}
    )


def test_waiting_run_resumes_across_instances(tmp_path):
    store = InMemoryRuntimeStore()
    database_path = tmp_path / "wf-cp.db"
    first = _runner(store, database_path, _approval_workflow())
    run = _seed_run(store)

    paused = first.run(run_id=run.id, definition=first.resolve("approval"), input={"amount": 100})
    assert paused.status == "waiting_for_human"

    # A fresh runner (new process) reattaches to the durable thread.
    second = _runner(store, database_path, _approval_workflow())
    result = second.resume(
        run_id=run.id, definition=second.resolve("approval"), response={"approved": True}
    )

    assert result.status == "completed"
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output["human_response"] == {"approved": True}
    assert saved.output["done"] is True


def test_failed_run_resumes_across_instances(tmp_path):
    store = InMemoryRuntimeStore()
    calls: dict = {}
    database_path = tmp_path / "wf-cp.db"
    first = _runner(store, database_path, _flaky_workflow(calls))
    run = _seed_run(store)

    failed = first.run(run_id=run.id, definition=first.resolve("flaky"), input={"amount": 100})
    assert failed.status == "failed"
    assert calls["flaky"] == 1

    second = _runner(store, database_path, _flaky_workflow(calls))
    result = second.resume(run_id=run.id, definition=second.resolve("flaky"))

    assert result.status == "completed"
    saved = RunManager(store).get_run(run.id)
    assert saved.status is RunStatus.COMPLETED
    assert saved.output["recovered"] is True
    assert saved.output["done"] is True
    assert calls["flaky"] == 2  # the failed node re-executed exactly once


def test_retry_starts_fresh_over_durable_thread(tmp_path):
    store = InMemoryRuntimeStore()
    calls: dict = {}
    database_path = tmp_path / "wf-cp.db"
    runner = _runner(store, database_path, _flaky_workflow(calls))
    run = _seed_run(store)
    first = runner.run(run_id=run.id, definition=runner.resolve("flaky"), input={"amount": 100})
    assert first.status == "failed"

    # Retry re-dispatches from scratch: the stale thread (channel values
    # from the failed attempt) must be cleared, not merged into.
    RunManager(store).requeue_run(run.id)
    retried = runner.run(run_id=run.id, definition=runner.resolve("flaky"), input={"amount": 100})

    assert retried.status == "completed"
    assert retried.state["recovered"] is True
    assert calls["flaky"] == 2

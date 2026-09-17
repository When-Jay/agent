"""Artifact chain tests (runtime-spec.md section 10).

Covers the V1 production/consumption loop: artifact persistence in both
stores, sandbox-backend production registration (deepagents-runtime-spec
section 7), adapter wiring, and the read-only API endpoints.
"""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.infrastructure.sqlalchemy_store import SQLAlchemyRuntimeStore
from agent_platform.runtime.agent import DeepAgentsRuntimeAdapter, PlatformSandboxBackend
from agent_platform.runtime.agent import adapter as adapter_module
from agent_platform.runtime.core import (
    Artifact,
    ArtifactStore,
    InMemoryRuntimeStore,
    RunManager,
    RunStatus,
    SessionManager,
)
from agent_platform.runtime.capabilities.tool_capability import InMemoryToolCapability
from agent_platform.runtime.dispatch import create_runtime_store
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import (
    ExecutionResult,
    FileUploadResult,
    SandboxSpec,
    WorkspaceMount,
)
from agent_platform.sandbox.registry import SandboxProviderRegistry

from test_agent_runtime import _FakeModel
from test_sandbox_core import FakeSandboxProvider


def _make_artifact(run_id: str, name: str, *, created_at=None) -> Artifact:
    return Artifact(
        id=str(uuid4()),
        run_id=run_id,
        name=name,
        uri=f"sandbox://sb/{name}",
        created_at=created_at or datetime.now(timezone.utc),
    )


def _make_run(store, *, runtime_type="agent"):
    runs = RunManager(store)
    apps = SessionManager(store)
    application = apps.create_application(name="demo")
    session = apps.create_session(application_id=application.id)
    return runs.create_run(
        application_id=application.id,
        session_id=session.id,
        runtime_type=runtime_type,
        status=RunStatus.QUEUED,
    )


# --- store persistence ----------------------------------------------------------


def test_inmemory_store_lists_artifacts_per_run_in_insertion_order():
    store = InMemoryRuntimeStore()
    run_a, run_b = str(uuid4()), str(uuid4())
    first = _make_artifact(run_a, "a-first")
    second = _make_artifact(run_a, "a-second")
    other = _make_artifact(run_b, "b-only")
    for artifact in (first, other, second):
        store.save_artifact(artifact)

    assert store.list_artifacts_for_run(run_a) == [first, second]
    assert store.list_artifacts_for_run(run_b) == [other]
    assert store.list_artifacts_for_run(str(uuid4())) == []


def test_sqlalchemy_store_lists_artifacts_for_run(tmp_path):
    store = SQLAlchemyRuntimeStore(f"sqlite:///{tmp_path / 'artifacts.db'}")
    run_a, run_b = str(uuid4()), str(uuid4())
    early = _make_artifact(run_a, "early", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    late = _make_artifact(run_a, "late", created_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    other = _make_artifact(run_b, "other")
    for artifact in (late, other, early):
        store.save_artifact(artifact)

    assert [a.name for a in store.list_artifacts_for_run(run_a)] == ["early", "late"]
    assert [a.name for a in store.list_artifacts_for_run(run_b)] == ["other"]
    assert store.list_artifacts_for_run(str(uuid4())) == []


def test_artifact_store_helper_creates_and_lists():
    store = InMemoryRuntimeStore()
    artifacts = ArtifactStore(store)
    run_id = str(uuid4())

    created = artifacts.create(run_id=run_id, name="report.txt", uri="sandbox://sb/report.txt")

    assert created.run_id == run_id
    assert created.metadata == {}
    assert artifacts.get(created.id) == created
    assert artifacts.list_for_run(run_id) == [created]
    assert artifacts.get(str(uuid4())) is None


# --- sandbox backend production ---------------------------------------------------


class _DeniedUploadProvider(FakeSandboxProvider):
    async def upload_files(self, sandbox, files):  # noqa: ANN001, ANN202
        return [FileUploadResult(path=f.path, ok=False, error="denied") for f in files]


def _backend(store, provider=FakeSandboxProvider, *, artifacts=True, run_id="run-1"):
    registry = SandboxProviderRegistry()
    registry.register("fake", provider() if isinstance(provider, type) else provider)
    manager = SandboxManager(registry, default_provider="fake")
    spec = SandboxSpec(
        image="fake:latest",
        tenant_id="tenant",
        user_id="user",
        session_id="session-1",
        workspace=WorkspaceMount(workspace_id="ws-session-1"),
    )
    sandbox = asyncio.run(manager.create(spec))
    return PlatformSandboxBackend(
        manager,
        sandbox.sandbox_id,
        artifacts=ArtifactStore(store) if artifacts else None,
        run_id=run_id if artifacts else None,
    )


def test_backend_upload_registers_artifacts_for_run():
    store = InMemoryRuntimeStore()
    backend = _backend(store)

    uploads = asyncio.run(backend.upload_files([("notes.txt", b"data"), ("out.csv", b"a,b")]))
    assert all(upload.error is None for upload in uploads)

    registered = ArtifactStore(store).list_for_run("run-1")
    assert [a.name for a in registered] == ["notes.txt", "out.csv"]
    assert registered[0].uri == f"sandbox://{backend.id}/notes.txt"
    assert registered[0].metadata == {"sandbox_id": backend.id, "size": 4}
    assert registered[1].metadata["size"] == 3


def test_backend_failed_upload_registers_no_artifact():
    store = InMemoryRuntimeStore()
    backend = _backend(store, provider=_DeniedUploadProvider)

    uploads = asyncio.run(backend.upload_files([("blocked.txt", b"data")]))
    assert uploads[0].error == "denied"

    assert ArtifactStore(store).list_for_run("run-1") == []


def test_backend_without_artifact_store_still_uploads():
    store = InMemoryRuntimeStore()
    backend = _backend(store, artifacts=False)

    uploads = asyncio.run(backend.upload_files([("notes.txt", b"data")]))

    assert uploads[0].error is None
    assert store.list_artifacts_for_run("run-1") == []


# --- truncated execution output ----------------------------------------------------


class _TruncatedExecuteProvider(FakeSandboxProvider):
    """Provider whose execute always reports a truncated large output."""

    def __init__(self, stdout: str) -> None:
        super().__init__()
        self._stdout = stdout

    async def execute(self, sandbox, request):  # noqa: ANN001, ANN202
        self.executions.append(request)
        return ExecutionResult(
            request_id=request.request_id,
            exit_code=0,
            stdout=self._stdout,
            stderr="",
            duration_ms=1,
            truncated=True,
        )


def test_backend_truncated_execute_persists_full_output_as_artifact():
    store = InMemoryRuntimeStore()
    provider = _TruncatedExecuteProvider("x" * 10)
    backend = _backend(store, provider)

    response = asyncio.run(backend.execute("generate-big-output"))

    assert response.truncated is True
    registered = ArtifactStore(store).list_for_run("run-1")
    assert len(registered) == 1
    path = f".platform/outputs/{provider.executions[0].request_id}.txt"
    assert registered[0].name == path
    assert registered[0].uri == f"sandbox://{backend.id}/{path}"
    assert registered[0].metadata["exit_code"] == 0
    assert registered[0].metadata["size"] == 10
    # The full output is retrievable from the sandbox output area.
    downloads = asyncio.run(backend.download_files([path]))
    assert downloads[0].content == b"x" * 10


def test_backend_normal_execute_registers_no_artifact():
    store = InMemoryRuntimeStore()
    backend = _backend(store)

    asyncio.run(backend.execute("echo hi"))

    assert store.list_artifacts_for_run("run-1") == []


def test_backend_truncated_execute_without_artifact_store_still_executes():
    provider = _TruncatedExecuteProvider("big output")
    backend = _backend(InMemoryRuntimeStore(), provider, artifacts=False)

    response = asyncio.run(backend.execute("generate-big-output"))

    assert response.truncated is True
    assert provider.files == {}  # nothing persisted without a run binding


def test_adapter_wires_artifact_store_into_backend(monkeypatch):
    store = InMemoryRuntimeStore()
    run = _make_run(store)
    registry = SandboxProviderRegistry()
    registry.register("fake", FakeSandboxProvider())
    manager = SandboxManager(registry, default_provider="fake")
    captured: dict = {}

    real_backend = PlatformSandboxBackend

    def spy_backend(sandbox_manager, sandbox_id, **kwargs):
        captured.update(kwargs, sandbox_id=sandbox_id)
        return real_backend(sandbox_manager, sandbox_id)

    class _StubAgent:
        async def ainvoke(self, state, config=None):  # noqa: ANN001
            return {"messages": [AIMessage("done")]}

    monkeypatch.setattr(adapter_module, "PlatformSandboxBackend", spy_backend)
    monkeypatch.setattr(adapter_module, "create_deep_agent", lambda **kwargs: _StubAgent())

    adapter = DeepAgentsRuntimeAdapter(
        store,
        model_factory=lambda spec: _FakeModel(messages=iter([AIMessage("hi")])),
        tool_capability=InMemoryToolCapability(),
        sandbox_manager=manager,
    )
    result = asyncio.run(adapter.run(run.id))

    assert result.status == "completed"
    assert captured["sandbox_id"]
    assert isinstance(captured["artifacts"], ArtifactStore)
    assert captured["artifacts"].list_for_run(run.id) == []


# --- API consumption ---------------------------------------------------------------


def _client(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'api.db'}"
    settings = Settings(database_url=database_url, celery_task_always_eager=True)
    return TestClient(create_app(settings)), create_runtime_store(database_url)


def test_list_run_artifacts_and_get_by_id(tmp_path):
    client, store = _client(tmp_path)
    run = _make_run(store)
    artifacts = ArtifactStore(store)
    first = artifacts.create(run_id=run.id, name="notes.txt", uri="sandbox://sb/notes.txt")
    second = artifacts.create(
        run_id=run.id, name="out.csv", uri="sandbox://sb/out.csv", metadata={"size": 3}
    )

    listing = client.get(f"/api/v1/runs/{run.id}/artifacts")
    assert listing.status_code == 200
    body = listing.json()
    assert [a["id"] for a in body["artifacts"]] == [first.id, second.id]
    # SQLite drops the UTC offset on readback; compare the instant, not the suffix.
    assert body["artifacts"][0] == {
        "id": first.id,
        "run_id": run.id,
        "name": "notes.txt",
        "uri": "sandbox://sb/notes.txt",
        "metadata": {},
        "created_at": first.created_at.replace(tzinfo=None).isoformat(),
    }

    single = client.get(f"/api/v1/artifacts/{second.id}")
    assert single.status_code == 200
    assert single.json()["name"] == "out.csv"
    assert single.json()["metadata"] == {"size": 3}


def test_list_run_artifacts_empty_for_run_without_artifacts(tmp_path):
    client, store = _client(tmp_path)
    run = _make_run(store)

    response = client.get(f"/api/v1/runs/{run.id}/artifacts")

    assert response.status_code == 200
    assert response.json() == {"artifacts": []}


def test_artifact_endpoints_return_404_for_unknown_ids(tmp_path):
    client, store = _client(tmp_path)
    _make_run(store)

    assert client.get(f"/api/v1/runs/{uuid4()}/artifacts").status_code == 404
    assert client.get(f"/api/v1/artifacts/{uuid4()}").status_code == 404


def test_artifacts_are_scoped_to_their_run(tmp_path):
    client, store = _client(tmp_path)
    run_a = _make_run(store)
    run_b = _make_run(store)
    artifacts = ArtifactStore(store)
    only_a = artifacts.create(run_id=run_a.id, name="a.txt", uri="sandbox://sb/a.txt")
    artifacts.create(run_id=run_b.id, name="b.txt", uri="sandbox://sb/b.txt")

    listing = client.get(f"/api/v1/runs/{run_a.id}/artifacts").json()

    assert [a["id"] for a in listing["artifacts"]] == [only_a.id]

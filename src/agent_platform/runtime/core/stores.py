from typing import Any
from uuid import uuid4

from agent_platform.runtime.core.events import RuntimeEvent
from agent_platform.runtime.core.interfaces import RuntimeStore
from agent_platform.runtime.core.models import (
    Application,
    Artifact,
    Checkpoint,
    Run,
    Session,
    State,
)


class InMemoryRuntimeStore:
    def __init__(self) -> None:
        self.applications: dict[str, Application] = {}
        self.sessions: dict[str, Session] = {}
        self.runs: dict[str, Run] = {}
        self.states: dict[str, State] = {}
        self.events: dict[str, list[RuntimeEvent]] = {}
        self.checkpoints: dict[str, Checkpoint] = {}
        self.checkpoints_by_run: dict[str, list[str]] = {}
        self.artifacts: dict[str, Artifact] = {}

    def save_application(self, application: Application) -> None:
        self.applications[application.id] = application

    def get_application(self, application_id: str) -> Application | None:
        return self.applications.get(application_id)

    def list_applications(self) -> list[Application]:
        return list(self.applications.values())

    def save_session(self, session: Session) -> None:
        self.sessions[session.id] = session

    def get_session(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    def list_sessions(self, application_id: str | None = None) -> list[Session]:
        sessions = self.sessions.values()
        if application_id is not None:
            sessions = [s for s in sessions if s.application_id == application_id]
        return list(sessions)

    def save_run(self, run: Run) -> None:
        self.runs[run.id] = run

    def get_run(self, run_id: str) -> Run | None:
        return self.runs.get(run_id)

    def list_runs(
        self, application_id: str | None = None, session_id: str | None = None
    ) -> list[Run]:
        runs = self.runs.values()
        if application_id is not None:
            runs = [r for r in runs if r.application_id == application_id]
        if session_id is not None:
            runs = [r for r in runs if r.session_id == session_id]
        return list(runs)

    def get_state(self, run_id: str) -> State | None:
        return self.states.get(run_id)

    def save_state(self, state: State) -> None:
        self.states[state.run_id] = state

    def save_event(self, event: RuntimeEvent) -> None:
        self.events.setdefault(event.run_id, []).append(event)

    def list_events(self, run_id: str) -> list[RuntimeEvent]:
        return list(self.events.get(run_id, []))

    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        self.checkpoints[checkpoint.id] = checkpoint
        self.checkpoints_by_run.setdefault(checkpoint.run_id, []).append(checkpoint.id)

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        return self.checkpoints.get(checkpoint_id)

    def latest_checkpoint_for_run(self, run_id: str) -> Checkpoint | None:
        checkpoint_ids = self.checkpoints_by_run.get(run_id, [])
        if not checkpoint_ids:
            return None
        return self.checkpoints[checkpoint_ids[-1]]

    def save_artifact(self, artifact: Artifact) -> None:
        self.artifacts[artifact.id] = artifact

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self.artifacts.get(artifact_id)


class CheckpointStore:
    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def create(self, *, run_id: str, state: dict[str, Any]) -> Checkpoint:
        checkpoint = Checkpoint(id=str(uuid4()), run_id=run_id, state=state)
        self._store.save_checkpoint(checkpoint)
        return checkpoint

    def get(self, checkpoint_id: str) -> Checkpoint | None:
        return self._store.get_checkpoint(checkpoint_id)

    def latest_for_run(self, run_id: str) -> Checkpoint | None:
        return self._store.latest_checkpoint_for_run(run_id)


class ArtifactStore:
    def __init__(self, store: RuntimeStore) -> None:
        self._store = store

    def create(
        self,
        *,
        run_id: str,
        name: str,
        uri: str,
        metadata: dict[str, Any] | None = None,
    ) -> Artifact:
        artifact = Artifact(
            id=str(uuid4()),
            run_id=run_id,
            name=name,
            uri=uri,
            metadata=metadata or {},
        )
        self._store.save_artifact(artifact)
        return artifact

    def get(self, artifact_id: str) -> Artifact | None:
        return self._store.get_artifact(artifact_id)

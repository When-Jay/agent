# Runtime Dispatch Specification

## 1. Purpose

Runtime Dispatch separates API request handling from long-running Runtime execution.

API creates and controls Runs.

Runtime Workers execute Runs.

Celery with Redis is the V1 dispatch mechanism.

PostgreSQL is the durable source of truth.

---

## 2. Architecture

```text
Client
  ↓
FastAPI
  ↓
PostgreSQL
  - Application
  - Session
  - Run
  - RuntimeEvent
  - Checkpoint
  - Artifact
  - Audit
  ↓
Celery enqueue(run_id)
  ↓
Redis broker
  ↓
Runtime Worker
  ↓
Run Orchestrator
  ↓
Agent Runtime Adapter / Workflow Runtime
```

Redis is not the durable system of record.

Redis may be used for:

* Celery broker
* Celery result backend if needed
* Live event fanout / stream
* short-lived locks where appropriate

PostgreSQL must store the durable Run state.

---

## 3. API Contract

Run creation:

```text
POST /api/v1/runs
```

Behavior:

1. Validate request.
2. Create Run in PostgreSQL with `status=queued`.
3. Enqueue Celery task with `run_id`.
4. Return Run metadata immediately.

The API must not call Agent Runtime or Workflow Runtime directly.

---

## 4. Worker Contract

Celery task input:

```text
run_id: str
```

Worker behavior:

1. Load Run from PostgreSQL.
2. Transition Run from `queued` to `running`.
3. Route by `runtime_type`.
4. Execute through Agent Runtime Adapter or Workflow Runtime.
5. Persist RuntimeEvents.
6. Persist Checkpoints and Artifacts.
7. Transition Run to `completed`, `failed`, or `cancelled`.

Worker execution must be idempotency-aware. Retried Celery tasks must not accidentally execute a completed Run again.

---

## 5. Run Status

V1 statuses:

```text
queued
running
completed
failed
cancelled
```

Optional later statuses:

```text
pausing
paused
resuming
waiting_for_human
retrying
```

---

## 6. Event Delivery

Runtime Worker writes every RuntimeEvent to PostgreSQL.

For live updates, Runtime Worker may also publish the same event to Redis Stream / PubSub.

API streaming reads from Redis for low latency and can fall back to PostgreSQL event replay.

---

## 7. Observability

Langfuse is the preferred V1 observability backend.

Runtime Worker should correlate:

```text
tenant_id
user_id
application_id
session_id
run_id
celery_task_id
trace_id
langfuse_trace_id
```

Langfuse failures must not fail Runtime execution.

---

## 8. Acceptance Criteria

* API creates a queued Run and enqueues a Celery task.
* Worker executes by `run_id`, not by embedded request payload.
* PostgreSQL stores durable Run state and event log.
* Redis is used for dispatch / live delivery only.
* API request handlers do not import Agent or Workflow execution modules.
* Worker retries do not re-execute terminal Runs.
* Langfuse trace emission is optional and non-blocking.

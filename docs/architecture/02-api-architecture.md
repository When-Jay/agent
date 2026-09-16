# 02-api-architecture.md

## 1. Purpose

API Layer 是 Platform 的统一入口。

API Layer 只负责：

* Authentication
* Authorization
* Request Validation
* Application Management
* Session Management
* Run Management
* Streaming
* File / Artifact Access
* Configuration Management

API Layer 不负责 Agent Loop 和 Workflow Execution。

---

## 2. API Structure

```text
/api/v1
├── applications
├── sessions
├── runs
├── workflows
├── agents
├── tools
├── knowledge
├── artifacts
├── evaluations
└── health
```

---

## 3. Execution API

核心概念：

```text
Application
Session
Run
```

基本关系：

```text
Application
   |
   +-- Session
         |
         +-- Run
         +-- Run
         +-- Run
```

API 创建 Run 后，将执行请求持久化到 PostgreSQL，并通过 Celery 将执行任务投递给 Runtime Worker。

API 不直接执行 Agent。

```text
Client
  ↓
API
  ↓
PostgreSQL: create Run(status=queued)
  ↓
Celery enqueue(run_id)
  ↓
Redis broker
  ↓
Runtime Worker
  ↓
Runtime executes Run
  ↓
PostgreSQL: status/events/checkpoints/artifacts
```

API request handler MUST NOT call Agent Runtime or Workflow Runtime directly.

API should return after Run creation and dispatch. Clients observe progress through Run query APIs and streaming APIs.

---

## 4. Streaming

支持：

```text
Run Event
    |
    +--> PostgreSQL durable event log
    |
    +--> Redis Stream / PubSub for low-latency delivery
             |
             v
            API
             |
             v
          Client
```

Streaming 不作为 Runtime 的核心业务逻辑。

Runtime 产生 Event。

API 负责将 Event 暴露给客户端。

PostgreSQL remains the durable source of truth. Redis streaming is an optimization for live updates.

---

## 5. Authentication

预留：

* User
* Tenant
* Organization
* Role
* Permission

所有资源必须具备明确的 ownership / tenant scope。

---



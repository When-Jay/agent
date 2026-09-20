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

## 6. API Hardening

已实现的加固层（中间件代码：`src/agent_platform/api/middleware.py`，
配置项：`src/agent_platform/config.py`，边缘层见
07-deployment-architecture.md 第 3 节）：

### 6.1 CORS

`CORS_ALLOW_ORIGINS` 为逗号分隔的 origin 白名单；**空值 = 仅同源
（CORS 禁用）**，默认安全。

### 6.2 限流（RateLimitMiddleware）

* 维度：per client-IP + method 的滑动窗口，超限返回 `429` +
  `Retry-After`。
* 后端：生产用 Redis ZSET（单次 pipeline），测试/单进程用内存实现。
* 豁免路径：`/api/v1/health`、`/api/v1/metrics`（健康探针与指标抓取
  不参与限流）。
* **信任边界**：以 socket peer IP 为键，不信任 `X-Forwarded-For`。
  反代之后逐 IP 语义失真，限流职责转移至可信边缘层（nginx），
  应用层限流为兜底。
* **Fail open**：Redis 后端故障时放行——可用性优先于限流精度。

### 6.3 请求体上限（BodyLimitMiddleware）

* 超过 `MAX_REQUEST_BODY_MB` 返回 `413`；`Content-Length` 上游短路，
  chunked 请求体缓冲至上限。
* 缓冲体**只 replay 一次**，随后 `receive()` 转发回原始通道——
  StreamingResponse 的 `listen_for_disconnect` 循环依赖
  `http.disconnect`，永久 stub 会造成无限自旋。

### 6.4 注册顺序

```text
CORS
  └── RateLimitMiddleware / BodyLimitMiddleware（内侧）
        └── 业务路由
```

preflight（OPTIONS）不参与限流与请求体检查；`RATE_LIMIT_ENABLED`
可整体关闭应用层限流。

---



# 07-deployment-architecture.md

本文档记录平台的生产部署架构：拓扑、并发容量模型、双层限流、安全加固与 CI。
它描述**已实现**的部署形态（docker-compose 单机 + nginx 边缘网关）以及配套的
配置项；Kubernetes 部署形态见文末"路线"一节。

对应的实现：`docker-compose.yml`、`.env.example`、`web/nginx.conf`、
`src/agent_platform/api/middleware.py`、`src/agent_platform/config.py`、
`.github/workflows/ci.yml`。

---

## 1. 部署拓扑

```text
                Client
                   |
                   v
        +---------------------+
        |  web (nginx)        |  边缘网关：per-IP 限流 + 请求体上限
        |  :3000              |  BACKEND_UPSTREAM / NGINX_* 模板变量
        +----------+----------+
                   |
        frontend   |            +------------------+
                   v            |  api (uvicorn)   |
        +---------------------+ |  :8000           |
        |  backend network    |-+  alembic upgrade |
        +---------------------+   + --workers       |
                   |                        |
     +-------------+--------------+   +-----+-----+
     v                            v   v           v
+-----------+              +-----------+   +-------------+
| postgres  |              |  redis    |   | beat        |
| :5432     |              | :6379     |   | (单实例)    |
| 持久化卷  |              | 密码保护  |   +-------------+
+-----------+              +-----+-----+
      ^                          |  broker / SSE fanout / steering
      |                          v
+------------------------------------------+
| worker (-Q default, gevent x8)           |  生产 Run 执行
| worker-eval (-Q evaluation, gevent x2)   |  评测/进化/巡检
+------------------------------------------+
```

服务职责与启动顺序：

| 服务 | 职责 | 启动依赖 |
| --- | --- | --- |
| `api` | 唯一执行数据库迁移的进程：`alembic upgrade head` 后启动 uvicorn | postgres/redis healthcheck |
| `worker` | Celery worker，消费 `default` 队列（Run 执行） | postgres/redis healthy；**api healthy**（表已建好） |
| `worker-eval` | Celery worker，消费 `evaluation` 队列 | 同上 |
| `beat` | 定时调度（评测巡检；cron 未配置时空转无害） | 同上；单实例即可 |
| `web` | nginx 边缘网关，反代 API | api healthy |

要点：

* **迁移只在 api 进程执行一次**：worker 依赖 api 的 healthcheck，
  保证消费任务时 schema 已就绪。
* `postgres`/`redis` 端口仅绑定 `127.0.0.1`，不暴露到局域网；
  redis 强制 `requirepass`（见 `.env.example`）。
* `REDIS_EVENT_FANOUT_ENABLED=1` 在多进程部署下**必须开启**：
  API 的 SSE 订阅依赖 worker 通过 Redis 发布的事件流，steering
  队列共享同一开关。
* **Sandbox warm pool**（默认关闭）：`SANDBOX_WARM_POOL_ENABLED=1`
  时，beat 追加注册 `agent_platform.sandbox.maintain_warm_pools`
  （周期 `SANDBOX_WARM_POOL_MAINTAIN_INTERVAL_SECONDS`，跑在
  `default` 队列，由 `worker` 消费），维护循环通过 Redis 维护锁
  防止 HA beat 双跑；池状态存于 Redis，`worker` 与 API 多进程均
  可原子 claim，任何池故障自动回落冷创建。配置见
  `docs/specs/sandbox-warm-pool-spec.md`。
* 模型密钥（`ANTHROPIC_API_KEY` 等）由宿主机环境变量透传，
  不写入镜像；模型选择在 application metadata 中配置
  `{'agent': {'model': '<provider>:<model-name>'}}`。
* docker sandbox 需要时才挂载 `/var/run/docker.sock`（默认注释掉）。

---

## 2. 并发容量模型

### 2.1 API 层

* `API_WORKERS`（默认 2）：`uvicorn --workers` 多进程。
* 每个进程持有独立的 SQLAlchemy 连接池；池配置仅在 Postgres
  方言生效（SQLite 路径不变），工厂位于
  `sqlalchemy_store.create_sqlalchemy_engine`。

### 2.2 数据库连接预算

**Postgres `max_connections` 必须覆盖：**

```text
api_workers × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
  + worker 池连接 + 运维余量
```

默认值：2 × (10 + 20) = 60 + 余量。`DB_POOL_RECYCLE`（默认 1800s）
防止连接被数据库/中间层静默回收。

### 2.3 Worker 层

* Run 执行是 IO 密集（LLM / 工具调用），Celery 使用 **gevent 协程池**
  而非 prefork：协程密度远高于进程，依赖 `celery[gevent]`。
* **队列分离**：`default`（生产 Run，并发 8）与 `evaluation`
  （评测/进化/巡检，并发 2）分别由 `worker` 与 `worker-eval`
  消费，防止长评测饿死生产 Run。
* 每个任务执行时重建 orchestrator，不跨任务共享内存状态。

---

## 3. 限流（双层）

限流在**边缘网关与应用内中间件**两层生效，语义互补：

```text
Client
  ↓
web (nginx)：per-IP limit_req（桶算法）+ client_max_body_size
  ↓
api (uvicorn)：RateLimitMiddleware（滑动窗口 429）
               BodyLimitMiddleware（413）
  ↓
业务路由（CORS 内层，prefork 预检先于限流）
```

### 3.1 边缘层（nginx）

`web/nginx.conf` 为 envsubst 模板：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `BACKEND_UPSTREAM` | `http://api:8000` | 反代上游 |
| `NGINX_API_RATE` | `30r/s` | per-IP 令牌速率 |
| `NGINX_API_BURST` | `60` | 允许的突发排队 |
| `NGINX_MAX_BODY_SIZE` | `10m` | 请求体上限（与 `MAX_REQUEST_BODY_MB` 对齐） |

### 3.2 应用层（`api/middleware.py`）

* **RateLimitMiddleware**：per client-IP + method 的滑动窗口，
  超限返回 `429` + `Retry-After`。生产用 Redis ZSET 后端
  （一次 pipeline 往返），测试/单进程用内存实现。
  `RATE_LIMIT_RPM`（默认 120）、`RATE_LIMIT_ENABLED` 可关。
  `/api/v1/health` 与 `/api/v1/metrics` 豁免——健康探针与
  指标抓取必须能承受客户端侧突发。
* **BodyLimitMiddleware**：超过 `MAX_REQUEST_BODY_MB`（默认 10）
  返回 `413`。`Content-Length` 直接短路拒绝；chunked 请求体缓冲
  至上限后**只 replay 一次**，随后转发回原始 `receive`——
  StreamingResponse 的 `listen_for_disconnect` 会循环等待
  `http.disconnect`，永久 stub 会造成无限自旋（曾挂起整个测试套件）。
* 中间件注册在 CORS **内侧**，preflight（OPTIONS）不参与限流与
  请求体检查。

### 3.3 信任边界

应用层限流以 **socket peer IP** 为键，**故意不信任**
`X-Forwarded-For`：置于反代之后时所有流量共享代理 IP，
逐 IP 语义会失真。因此：

* 单机直连：应用层限流语义正确；
* 反代部署（当前形态）：**限流由 nginx 边缘层承担**，
  应用层限流作为兜底/防误配置。

### 3.4 Fail open

Redis 限流后端故障时记录告警并**放行**：可用性优先于限流精度。
这是有意决策——限流器不应成为全站故障点。

---

## 4. API 加固清单

| 项 | 实现 | 配置 |
| --- | --- | --- |
| CORS 白名单 | 空列表 = 仅同源（CORS 禁用） | `CORS_ALLOW_ORIGINS` |
| 请求体上限 | 413 中间件 | `MAX_REQUEST_BODY_MB` |
| 限流 | 429 中间件（Redis 滑动窗口） | `RATE_LIMIT_ENABLED` / `RATE_LIMIT_RPM` |
| 边缘限流 | nginx limit_req | `NGINX_API_RATE` / `NGINX_API_BURST` |
| 边缘体上限 | nginx client_max_body_size | `NGINX_MAX_BODY_SIZE` |
| 数据库不外露 | 端口绑定 127.0.0.1 | compose |
| Redis 认证 | requirepass | `REDIS_PASSWORD` |
| 凭证注入 | 宿主机环境变量透传 | `.env.example` 全量清单 |
| 健康检查 | 每服务 healthcheck + 启动依赖 | compose |
| 认证鉴权 | **未实现（有意）**：由接入方对接自有认证系统 | — |

---

## 5. 数据库迁移

* Alembic 迁移链在 `alembic/`（`alembic upgrade head` 执行入口）。
* **api 是唯一迁移执行者**（compose command），worker 等待 api
  healthcheck 后启动，避免并发迁移与表未建时消费任务。
* 本地开发 / 测试可用 SQLite 直连，迁移仅 Postgres 路径必需。

---

## 6. CI

`.github/workflows/ci.yml`，push main / PR 触发，三个 job：

```text
test          pip install .[dev] && pytest -q
web-build     npm ci && npm run build (node 20, web/)
docker-build  docker build .（主镜像可构建性）
```

---

## 7. 可观测性接线

* `LANGFUSE_*` 配置后启用 Langfuse 导出；未配置时平台正常运行。
* `LOG_LEVEL` 控制运行时日志级别。
* `EVALUATION_PATROL_CRON`（默认空 = 不注册 beat 调度）+
  `EVALUATION_PATROL_ASSET`（默认 `regression-set`）驱动巡检任务。

---

## 8. Kubernetes 部署路线

**已实现**：Sandbox 的 Kubernetes Provider（plan 042）——沙箱负载
可以按 1 Sandbox = 1 Pod 在 K8s 集群中执行（见 06-sandbox-architecture
第 11 节与 docs/plans/042-k8s-sandbox.md）。

**未实现（有意保留）**：平台自身的 K8s 化部署。届时需要：

* api → Deployment + Service（多副本；连接池预算公式不变，
  注意 × 副本数）
* worker / worker-eval → Deployment（沿用队列分离）
* 迁移 → initContainer / Job（保持"单执行者"原则）
* nginx 边缘 → Ingress（限流注解）或保留 nginx Deployment
* HPA / PDB / 资源配额

在此实现之前，请勿基于推测新增部署脚本。

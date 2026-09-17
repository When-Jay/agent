# 03-mcp-gateway-architecture.md

## 1. Purpose

MCP Gateway 是统一 Tool Access Layer。

它不是简单的 MCP Proxy。

主要负责：

* Tool Registry
* Tool Discovery
* Tool Permission
* Tool Routing
* Tool Invocation
* Tool Isolation
* Timeout
* Retry
* Cancellation
* Audit
* Risk Control
* Trace
* Tool Result Handling

---

## 2. Architecture

```text
Agent Runtime
      |
      v
MCP Gateway
      |
      +-- Tool Registry
      |
      +-- Permission
      |
      +-- Risk Control
      |
      +-- Routing
      |
      +-- Invocation
      |
      +-- Audit
      |
      +-- Trace
      |
      +---------+---------+
                |
        +-------+-------+
        |               |
       MCP             Native
      Server            Tool
```

---

## 3. Tool Lifecycle

```text
Register
   ↓
Validate
   ↓
Policy Check
   ↓
Available
   ↓
Discover
   ↓
Authorize
   ↓
Invoke
   ↓
Validate Result
   ↓
Audit
```

---

## 4. Security Boundary

Tool Metadata、Tool Input、Tool Output 都属于不可信输入。

Gateway 必须预留：

* Permission Check
* Sensitive Data Detection
* Prompt Injection Detection
* Output Risk Detection
* Resource Limit
* Timeout
* Audit

---

## 5. V1

实现：

* Tool Registry
* MCP Tool Invocation
* Permission
* Timeout
* Basic Retry
* Audit
* Trace

暂不实现复杂自动 Tool Discovery / Agentic Tool Selection。

---

## 6. Transport and Isolation (V2)

平台禁止本地执行 stdio MCP server。

* http / sse server：平台通过官方 MCP SDK 客户端直连。
* stdio server：配置中的 command/env 交由沙箱运行（runner 模式）——
  runner 镜像在沙箱内把 stdio server 桥接为 Streamable HTTP，平台只讲
  HTTP，从网关视角 stdio 与 http server 无差别。
* Runner 生命周期 = server 会话生命周期：懒创建、健康检查、空闲销毁。
* 沙箱契约需要扩展端口暴露能力（见 sandbox-spec）。

工具在网关内以 `{server}__{tool}` 命名空间寻址，权限与审计使用命名空间名。

---

## 7. Idempotency (V2)

MCP 协议无幂等原语，网关按声明的工具类别执行重试策略：

* `readonly` / `idempotent`：可重试 dispatch 错误与超时。
* `mutating`（默认，保守）：仅可重试"未送达"错误；服务端 `isError`
  是最终结果，永不重试。
* 每次逻辑调用生成幂等键，经请求 `_meta` 传递（协作 server 可去重），
  网关侧维护短 TTL 去重窗口，作为本层权威防线。

---

## 8. Credentials (V2)

* 配置只存 `credential_ref` 引用，不存明文；解析通过 CredentialResolver
  端口（V2 提供 env/config 后端，secret manager 为保留能力）。
* 注入点：http/sse 在会话建立时注入 header；stdio/runner 经沙箱 secret
  机制注入 env，绝不进命令行。
* 会话按 (server, 凭据身份) 缓存，支持每用户委托凭据。
* 审计与日志永不记录凭据值、header/env 值、参数值与输出内容（仅记
  摘要与大小）。

---

## 9. Implementation

V2 设计与分阶段交付见 `docs/specs/mcp-gateway-spec.md`。
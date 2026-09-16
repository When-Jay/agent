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
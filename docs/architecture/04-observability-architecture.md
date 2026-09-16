
# 04-observability-architecture.md

## 1. Purpose

Observability 负责回答：

> Runtime 发生了什么？

核心对象：

```text
Trace
Span
Event
Log
Metric
Run
```

---

## 2. Architecture

```text
Runtime
   |
   +---- Events
   |
   +---- Metrics
   |
   +---- Logs
   |
   v
Observability Layer
   |
   +-- OpenTelemetry
   +-- Langfuse
   +-- Metrics Backend
   +-- Log Backend
```

---

## 2.1 V1 Backend

V1 preferred implementation:

* Langfuse for LLM / agent / tool / workflow trace visualization
* OpenTelemetry-compatible trace identifiers for correlation
* RuntimeEvent as the platform-normalized durable event log

Langfuse adapters consume Runtime events and LangChain / LangGraph callbacks.

Runtime execution must continue to work if Langfuse is disabled or temporarily unavailable.

---

## 3. Observability Dimensions

### Runtime

* Run Duration
* Run Success Rate
* Failure Rate
* Retry
* Timeout

### LLM

* Latency
* Token Usage
* Cost
* Model
* Error Rate

### Tool

* Tool Latency
* Tool Error
* Timeout
* Retry
* Invocation Count

### Agent

* Turn Count
* Tool Count
* Context Size
* Stop Reason
* Budget Consumption

### Workflow

* Node Duration
* Node Failure
* Retry
* Branch
* Workflow Completion

---

## 4. Principle

Observability 不参与正常执行决策。

```text
Runtime
   |
   +------> Observability
   |
   +------> Evaluation
```

而不是：

```text
Runtime
   |
   v
Observability
   |
   v
Runtime
```

---

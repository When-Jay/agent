# MCP Gateway Specification

This spec details the gateway expansion beyond V1 (03-mcp-gateway-architecture.md
sections 1-5). V1 (tool registry, invocation, permission, timeout, basic retry,
audit) is implemented in `src/agent_platform/mcp/` (gateway.py, source.py,
audit.py). This document specifies V2: transport isolation, invocation
hardening, and credential handling. The architecture document remains the
source of truth for boundaries; this spec is the source of truth for
implementation detail.

## 1. Goals

1. Platform never executes stdio MCP servers locally; stdio servers run
   inside sandbox workloads and are reached over HTTP.
2. Tool invocation is safe to retry under an explicit per-tool idempotency
   policy, with no accidental duplicate side effects.
3. User and server credentials are referenced, resolved, and injected at
   well-defined boundaries and never logged.
4. Tool inputs are validated against advertised schemas; outputs are size-
   bounded and treated as untrusted content.

## 2. Non-Goals

* Automatic or agentic tool discovery (unchanged from V1).
* Prompt-injection detection / content filtering (Evaluation's concern).
* A production secret manager integration (reserved capability; V2 ships
  the resolver port plus a config/env-backed adapter).
* Caching tool results (tool listing cache only, section 8).
* Multi-tenant server catalogs (platform-level catalog, per-application
  allowlist; tenant-scoped catalogs are reserved).

## 3. Module Boundaries

Unchanged dependency direction:

```text
Agent Runtime -> ToolCapability (runtime.capabilities) -> MCP Gateway (mcp/)
                                                          |
                                       ports: McpToolSource, CredentialResolver,
                                              ServerRunner, AuditSink
```

* `mcp/` never imports the MCP SDK, the sandbox stack, or infrastructure.
  All external effects enter through ports (Protocol), injected at the
  composition root (dispatch).
* The official `mcp` SDK is a runtime dependency used by the composition
  root to build sessions; the SDK-free `AsyncMcpSession` Protocol
  (source.py) stays the internal port so the bridge remains testable.
* Sandbox-backed runners implement the `ServerRunner` port; the gateway
  does not know SandboxManager exists.

## 4. Server Configuration

Server declarations live in platform settings (platform-level catalog):

```json
{
  "name": "github",
  "transport": "http | sse | stdio",
  "url": "https://...",            // http/sse
  "command": ["npx", "..."],       // stdio (runner-hosted)
  "env": {"...": "..."},           // stdio, non-sensitive only
  "credential_ref": "github-main", // optional, see section 9
  "side_effects": "readonly | idempotent | mutating",   // default: mutating
  "timeout_seconds": 30,
  "max_retries": 0,
  "max_concurrency": 8
}
```

* `side_effects` is declared by configuration, never inferred. The
  conservative default (`mutating`) means no retries after dispatch.
* Applications select a subset via metadata allowlist; the gateway's
  `allowed_tools` permission (V1) operates on namespaced names (section 5).

## 5. Tool Namespacing

* MCP tool identity inside the gateway is `{server}__{tool}` (double
  underscore; MCP tool names allow `[a-zA-Z0-9_-]`, so `__` cannot collide
  with native names that avoid it).
* `register_server` claims namespaced names; the descriptor keeps the
  server-local name for upstream calls.
* A collision between a namespaced name and a native tool name is a
  registration error (existing `_claim` behavior).
* Permission allowlists and audit entries use the namespaced name.

## 6. Transport and Isolation

### 6.1 http / sse servers

The composition root injects a **session factory** into `HttpMcpToolSource`
(sessions.py): a context manager that connects the official-SDK client
(transport + `ClientSession` + initialize) and yields the ready session.
Amended per implementation finding: anyio cancel scopes are task-bound, so
the session must be connected in ONE task on the holder's background loop —
the holder runs the factory itself; pre-connected sessions handed across
loops are unsupported. `SdkMcpToolSource` remains for already-connected
in-process sessions (tests, in-memory servers).

### 6.2 stdio servers (runner model)

Local subprocess execution is prohibited. A stdio declaration is materialized
as a sandbox workload:

1. The composition root injects a `ServerRunner` port.
2. `SandboxRunner` (mcp adapter over SandboxManager) creates a sandbox from
   a runner image with the configured command/env; the image hosts a
   sidecar that bridges the stdio server to Streamable HTTP inside the
   sandbox network.
3. The platform connects its SDK session to the sandbox endpoint; from
   here on, http and stdio servers are indistinguishable to the gateway.
4. Runner lifecycle: created lazily at registration, health-checked
   (MCP ping), destroyed on idle TTL or gateway shutdown. Destroy is
   best-effort and never blocks callers.

### 6.3 Required sandbox contract extension

`SandboxSpec`/`SandboxInfo` currently have no port/endpoint exposure. The
sandbox contract needs: declared ports on `SandboxSpec` and a reachable
endpoint address on `SandboxInfo` (host:port reachable from the API/worker
network). This is a cross-module change: it must be specified in
sandbox-spec.md before implementation (change policy, section 9). The
docker provider implements it first; the k8s provider follows 042-k8s-sandbox.

### 6.4 Secrets in runners

Runner env carrying credentials is injected through the provider's secret
mechanism (K8s Secret mount / docker env resolved at create time), never
through command-line arguments and never stored in run state.

## 7. Idempotency and Retry

### 7.1 Error classification

* `dispatch_error`: the request never reached the server (connect failure,
  DNS, TLS). Safe to retry for all classes.
* `timeout_error`: unknown server progress. Retriable for `readonly` only.
* `server_error` (`isError` result): the tool executed and reported
  failure. A final outcome — never retried, surfaced as an error result.
* `protocol_error`: malformed response. Not retried.

### 7.2 Retry policy

`attempts = 1 + max_retries`, with exponential backoff, applied per
`side_effects` class:

| class     | dispatch_error | timeout_error | server_error |
|-----------|----------------|---------------|--------------|
| readonly  | retry          | retry         | final        |
| idempotent| retry          | retry         | final        |
| mutating  | retry          | final         | final        |

### 7.3 Idempotency key

For every logical invocation the gateway generates a key and sends it in
the request `_meta` (`"platform.idempotency_key"`), so cooperating servers
can deduplicate. The gateway keeps a short-TTL (default 60s) map of
in-flight/recent `(server, tool, key)` entries: retries of one logical
call reuse the key; concurrent duplicate calls with the same key within
the TTL are rejected as `duplicate` (error result) rather than double-
dispatched. Upstream cooperation is best-effort; the map is authoritative
at the gateway layer.

## 8. Tool Listing Cache

`list_tools` results are cached per server with a TTL (default 5 min) and
 invalidated on reconnect. Invocation results are never cached.

## 9. Credentials

### 9.1 Model

* Server configs reference credentials by `credential_ref`; resolved
  values never appear in configuration, state, events, or logs.
* `CredentialResolver` port (mcp/ Protocol):
  `resolve(ref, context) -> CredentialMaterial` where material is
  `{headers: {...}, env: {...}}` and `context` carries the requesting
  session/user identity for per-user delegation (OAuth tokens).
* V2-C status: the port and the env-backed adapter are implemented;
  `context` (per-user identity) is RESERVED — `ToolCallRequest` does not
  carry identity yet, so V2-C resolves platform identity only. Propagating
  identity through requests is a follow-up change spanning the capability
  contract and agent loop.
* Session cache is keyed by `(server, credential fingerprint)`; per-user
  credentials produce per-identity sessions (bounded by `max_concurrency`
  and idle TTL). The cache exists (`SessionPool`); identity propagation
  is the remaining piece.

### 9.2 Injection points

* http/sse: `CredentialMaterial.headers` applied at session construction.
* stdio/runner: `CredentialMaterial.env` passed to the runner at sandbox
  creation (section 6.4).

### 9.3 Redaction

* Audit entries record: namespaced tool, server, outcome, duration,
  argument byte-size and SHA-256 digest, `credential_ref` name. They never
  record: header/env values, argument values, resolved credentials,
  tool output bodies (output size only).
* Gateway logs follow the same rule; `_meta` and headers are excluded from
  any debug dumping.

## 10. Input Validation

Before dispatch, arguments are validated against the advertised
`inputSchema` (JSON Schema subset: type, required, properties,
additionalProperties, enum, items; no `$ref`/remote resolution — documented
limitation). Validation failure returns a model-visible error result
(naming the failing path) so the agent loop can correct; it is not a
gateway exception.

## 11. Output Handling

* Output size limit (default 256 KiB per call): larger outputs are
  truncated with an explicit marker (`[truncated by mcp gateway, N bytes
  total]`). Persisting the full output as an Artifact requires a run
  binding the gateway does not have; that wiring lives in the tool
  middleware layer (which knows run_id) and is deferred — mechanism:
  middleware observes truncation markers and uploads to ArtifactStore.
* Output is untrusted content entering the model context. The gateway
  records its source in audit; content-level risk detection belongs to
  Evaluation (out of scope).

## 12. Connection Lifecycle

Per `(server, credential identity)` session holder (implemented as
`HttpMcpToolSource`):

1. Connect lazily at first use (or at registration for tool listing).
2. Health: MCP `ping` (SDK `send_ping`) before reuse after idle; on
   failure, kill the connection and make ONE transparent reconnect per
   call; further recovery is lazy — a failed connect arms a backoff gate
   (default 2s) that refuses subsequent calls until it expires.
3. Circuit breaker: consecutive transport failures (connect/call) open the
   circuit after a threshold (default 5) for a cooldown (default 30s);
   half-open single probe succeeds -> close. Server-reported failures
   (`is_error` results) never count — the server is alive and answered.
4. Concurrency cap per server (`max_concurrency`, default 8): excess calls
   fail fast after `acquire_timeout` (10s default) instead of queueing
   unboundedly.
5. Cancellation: when the calling run is cancelled/paused, in-flight
   invocations receive cancellation through the existing middleware
   timeout budget; long-runner teardown is best-effort.

## 13. Observability

* Audit: extended `AuditEntry` (section 9.3) remains the always-on
  gateway-local record; sink failure never breaks invocation (existing
  contract).
* Runtime events: TOOL_CALL_* events already carry invocations into the
  observability pipeline; no gateway-side metric duplication.
* Trace propagation: `traceparent` in request `_meta` — reserved.

## 14. Delivery Stages

| Stage | Scope | Depends on |
|-------|-------|------------|
| A. SDK adoption | add `mcp` dependency; in-memory-transport integration test pinning bridge assumptions (list_tools/call_tool shapes, isError, _meta) — DONE | — |
| B. Invocation hardening | namespacing, input validation, side_effects classes + retry policy, idempotency map, output truncation, audit redaction — DONE | — |
| C. HTTP transport + credentials | official-SDK session holder (http/sse), CredentialResolver port + env adapter, session cache, circuit breaker, concurrency cap, composition wiring — DONE (per-user identity propagation deferred, section 9.1) | A |
| D. stdio isolation | sandbox port-exposure contract (sandbox-spec update), runner image contract, ServerRunner port + SandboxRunner adapter (docker provider), k8s later (042) | sandbox contract change |

Each stage ships with unit tests and lands as an independent commit.
Stage D additionally requires the sandbox-spec.md update to be reviewed
first (change policy, section 9).

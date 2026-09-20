"""Kubernetes SandboxProvider (plan 042).

Maps the platform sandbox contract onto Kubernetes Pods: one sandbox is
one Pod with a hold command; commands run through the exec API. The
kubernetes SDK is imported lazily inside methods so the sandbox core and
the security-configuration unit tests do not require the SDK or a
cluster.

Security posture applied on every Pod (plan 042 section 5/6):

* pod-level ``runAsNonRoot`` + ``seccompProfile=RuntimeDefault``
  (``runAsNonRoot`` is relaxed only when ``container_user`` metadata
  explicitly requests uid 0)
* numeric non-root user ``65534:65534`` by default (override via spec
  metadata ``container_user``)
* ``privileged=false``, all capabilities dropped,
  ``allowPrivilegeEscalation=false``
* read-only root filesystem (override via metadata ``read_only_rootfs``)
* ``automountServiceAccountToken=false``: the sandbox never receives
  Kubernetes API credentials
* ``hostNetwork``/``hostPID``/``hostIPC`` disabled, ``restartPolicy=Never``
* CPU / memory / ephemeral-storage as requests+limits derived from
  ``SandboxResources``
* only explicitly configured environment variables (host env is never
  inherited)

Workspace: an ``emptyDir`` by default, or a pre-created PVC named by
spec metadata ``workspace_pvc``. Destroy deletes the Pod (and the
provider's NetworkPolicies) but never the PVC (plan 042 section 15).

Network mapping (``NetworkPolicy`` per sandbox, named
``<pod-name>-netpol``, targeting only this pod via its sandbox-id
label): ``NONE`` -> deny all ingress+egress; ``INTERNAL`` -> same
namespace only plus cluster DNS; ``ALLOWLIST`` -> DNS plus ipBlock rules
derived from the allowlist (CIDR required for non-IP hosts; DNS-name
allowlisting needs an egress proxy and is out of scope); ``INTERNET_ONLY``
-> deny egress to ``cluster_cidrs`` when the provider is constructed
with them, otherwise unrestricted (documented limitation: cluster-CIDR
knowledge is deployment-specific); ``FULL`` -> unrestricted.

Timeout: commands are wrapped with ``timeout -s KILL <seconds>`` inside
the container (probed once per pod) with a client-side deadline as
backstop, mirroring the docker provider.

Exec protocol notes: k8s exec carries no per-call cwd/env, so the
command is composed as ``timeout ... env K=V /bin/sh -c 'cd <cwd> && ...'``.
Upload streams a tar (built by the shared docker-provider helper) into
``tar -xf - -C <mount>`` over exec stdin; stdin EOF is signaled with an
empty stdin frame (apiserver websocket protocol). Download streams
``tar -cf - <rel> | base64`` back over stdout and decodes it client-side.
"""

import asyncio
import base64
import logging
import math
import posixpath
import re
import shlex
import time
from typing import Any

from agent_platform.sandbox.errors import (
    ProviderError,
    SandboxUnavailable,
)
from agent_platform.sandbox.models import (
    Endpoint,
    ExecutionRequest,
    ExecutionResult,
    FileDownloadResult,
    FileUpload,
    FileUploadResult,
    HealthStatus,
    NetworkMode,
    Sandbox,
    SandboxSpec,
    WorkspaceMount,
)
from agent_platform.sandbox.providers.docker import (
    _FORBIDDEN_MOUNT_ROOTS,  # sibling module: same forbidden bind roots
    ensure_under_mount,
    first_file_from_tar,
    upload_tar,
)

logger = logging.getLogger(__name__)

DEFAULT_USER = "65534:65534"
DEFAULT_MOUNT = "/workspace"
HOLD_COMMAND = ["tail", "-f", "/dev/null"]
WORKSPACE_VOLUME = "workspace"
LABEL_PREFIX = "agent-platform.io"
_POD_READY_POLL = 0.5
_TIMEOUT_GRACE = 5.0
_STDIN_CHUNK = 32 * 1024
_FILE_OP_TIMEOUT = 60.0

# Transient startup states that should keep us waiting instead of failing.
_TRANSIENT_WAITING = {"ContainerCreating", "PodInitializing", "PodScheduled"}


# --- pure helpers (unit-testable without kubernetes SDK or cluster) ----------


def pod_name(sandbox_id: str) -> str:
    return f"agent-platform-sbx-{sandbox_id}"


def sanitize_label_value(value: str) -> str:
    """Kubernetes label values: <=63 chars, alphanumeric/-/_/. edges alnum."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", str(value))[:63].strip("-._")
    return cleaned or "unknown"


def pod_labels(spec: SandboxSpec) -> dict[str, str]:
    return {
        f"{LABEL_PREFIX}/sandbox-id": sanitize_label_value(spec.sandbox_id),
        f"{LABEL_PREFIX}/tenant-id": sanitize_label_value(spec.tenant_id),
        f"{LABEL_PREFIX}/user-id": sanitize_label_value(spec.user_id),
        f"{LABEL_PREFIX}/session-id": sanitize_label_value(spec.session_id),
        f"{LABEL_PREFIX}/managed": "true",
    }


def parse_user(user: str) -> tuple[int, int]:
    """Parse ``uid:gid`` (or bare ``uid``) into ints."""
    try:
        if ":" in user:
            uid, gid = user.split(":", 1)
            return int(uid), int(gid)
        return int(user), int(user)
    except ValueError as exc:
        raise ProviderError(f"invalid container_user {user!r}") from exc


def validate_mount_path(mount_path: str) -> None:
    """Reject mount targets that would expose the node or credentials."""
    normalized = posixpath.normpath(mount_path)
    if normalized == "/" or any(
        normalized == root or normalized.startswith(root + "/")
        for root in _FORBIDDEN_MOUNT_ROOTS
    ):
        raise ProviderError(f"unsafe workspace mount path: {mount_path}")


def workspace_volume(spec: SandboxSpec) -> dict:
    pvc = spec.metadata.get("workspace_pvc")
    if pvc:
        return {"name": WORKSPACE_VOLUME, "persistentVolumeClaim": {"claimName": str(pvc)}}
    return {"name": WORKSPACE_VOLUME, "emptyDir": {}}


def container_resources(resources) -> dict:
    quantities = {
        "cpu": resources.cpu,
        "memory": resources.memory,
        "ephemeral-storage": resources.ephemeral_storage,
    }
    return {"requests": dict(quantities), "limits": dict(quantities)}


def build_pod_manifest(spec: SandboxSpec, namespace: str | None = None) -> dict:
    """Pure mapping SandboxSpec -> Pod manifest (dict accepted by CoreV1Api)."""
    workspace = spec.workspace or WorkspaceMount(workspace_id="")
    if not workspace.workspace_id:
        raise ProviderError("kubernetes provider requires spec.workspace.workspace_id")
    mount_path = posixpath.normpath(workspace.mount_path or DEFAULT_MOUNT)
    validate_mount_path(mount_path)

    user = str(spec.metadata.get("container_user", DEFAULT_USER))
    uid, gid = parse_user(user)
    read_only = bool(spec.metadata.get("read_only_rootfs", True))
    hold = spec.metadata.get("hold_command", HOLD_COMMAND)

    metadata: dict[str, Any] = {"name": pod_name(spec.sandbox_id), "labels": pod_labels(spec)}
    if namespace:
        metadata["namespace"] = namespace

    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": metadata,
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "hostNetwork": False,
            "hostPID": False,
            "hostIPC": False,
            "securityContext": {
                "runAsNonRoot": uid != 0,
                "seccompProfile": {"type": "RuntimeDefault"},
                "fsGroup": gid,
            },
            "volumes": [workspace_volume(spec)],
            "containers": [
                {
                    "name": "sandbox",
                    "image": spec.image,
                    "command": list(hold),
                    "workingDir": mount_path,
                    "env": [{"name": k, "value": str(v)} for k, v in spec.env.items()],
                    "resources": container_resources(spec.resources),
                    "securityContext": {
                        "runAsUser": uid,
                        "runAsGroup": gid,
                        "privileged": False,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": read_only,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [
                        {"name": WORKSPACE_VOLUME, "mountPath": mount_path}
                    ],
                }
            ],
        },
    }
    if spec.ports:
        manifest["spec"]["containers"][0]["ports"] = [
            {"name": p.name, "containerPort": p.container_port, "protocol": "TCP"}
            for p in spec.ports
        ]
    return manifest


def _cidr(entry: str) -> str:
    text = entry.strip()
    return text if "/" in text else f"{text}/32"


def build_network_policies(
    spec: SandboxSpec, cluster_cidrs: tuple[str, ...] = ()
) -> list[dict]:
    """NetworkPolicy manifests targeting only this sandbox pod.

    Returns an empty list for modes that need no policy (documented
    limitation for ``INTERNET_ONLY`` without ``cluster_cidrs``).
    """
    selector = {
        "matchLabels": {f"{LABEL_PREFIX}/sandbox-id": sanitize_label_value(spec.sandbox_id)}
    }
    base = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": f"{pod_name(spec.sandbox_id)}-netpol"},
        "spec": {"podSelector": selector, "policyTypes": ["Ingress", "Egress"]},
    }
    mode = spec.network_policy.mode
    if mode is NetworkMode.NONE:
        # No ingress/egress rules -> deny all traffic in both directions.
        return [base]

    same_ns_ingress = [{"from": [{"podSelector": {}}]}]
    dns_egress = [
        {
            "to": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                    }
                }
            ],
            "ports": [
                {"protocol": "UDP", "port": 53},
                {"protocol": "TCP", "port": 53},
            ],
        }
    ]

    if mode is NetworkMode.INTERNAL:
        egress = [{"to": [{"podSelector": {}}]}] + dns_egress
        policy = {**base["spec"], "ingress": same_ns_ingress, "egress": egress}
        return [{**base, "spec": policy}]

    if mode is NetworkMode.ALLOWLIST:
        egress: list[dict] = list(dns_egress)
        blocks = [_cidr(entry) for entry in spec.network_policy.allowlist if entry.strip()]
        if blocks:
            egress.append({"to": [{"ipBlock": {"cidr": c}} for c in blocks]})
        policy = {**base["spec"], "ingress": same_ns_ingress, "egress": egress}
        return [{**base, "spec": policy}]

    if mode is NetworkMode.INTERNET_ONLY and cluster_cidrs:
        policy = {
            **base["spec"],
            "egress": [
                {
                    "to": [
                        {
                            "ipBlock": {
                                "cidr": "0.0.0.0/0",
                                "except": [c.strip() for c in cluster_cidrs],
                            }
                        }
                    ]
                }
            ],
        }
        return [{**base, "spec": policy}]

    # INTERNET_ONLY without cluster_cidrs, and FULL: unrestricted.
    return []


def resolve_endpoints(ports, pod_ip: str | None) -> list[Endpoint]:
    """Pods are addressed directly on the cluster network (no publish step)."""
    if not ports:
        return []
    if not pod_ip:
        raise ProviderError(
            "declared ports could not be resolved: pod has no assigned IP"
        )
    return [Endpoint(name=p.name, address=f"{pod_ip}:{p.container_port}") for p in ports]


def build_exec_argv(
    command: str | list[str],
    cwd: str = DEFAULT_MOUNT,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
    wrapper_available: bool = True,
) -> list[str]:
    """Compose the exec argv: k8s exec has no per-call cwd/env support."""
    prefix: list[str] = []
    if wrapper_available:
        prefix += ["timeout", "-s", "KILL", str(max(1, math.ceil(timeout)))]
    if env:
        prefix += ["env", *[f"{k}={v}" for k, v in env.items()]]
    if isinstance(command, str):
        script = f"cd {shlex.quote(cwd)} && {command}"
        return [*prefix, "/bin/sh", "-c", script]
    if not command:
        raise ProviderError("empty command")
    script = f"cd {shlex.quote(cwd)} && exec \"$@\""
    return [*prefix, "/bin/sh", "-c", script, "sh", *command]


def download_argv(path: str, mount: str) -> list[str]:
    """``tar -cf - <rel> | base64`` so binary data survives the text channel."""
    ensure_under_mount(path, mount)
    rel = posixpath.relpath(posixpath.normpath(path), mount)
    pipeline = f"tar -cf - {shlex.quote(rel)} | base64"
    return ["/bin/sh", "-c", f"cd {shlex.quote(mount)} && {pipeline}"]


def decode_base64_output(text: str) -> bytes:
    return base64.b64decode("".join(text.split()))


def pod_ready(pod) -> bool:
    """True once the phase is Running and every container state is running."""
    status = getattr(pod, "status", None)
    if getattr(status, "phase", None) != "Running":
        return False
    statuses = getattr(status, "container_statuses", None) or []
    if not statuses:
        return False
    return all(
        getattr(getattr(cs, "state", None), "running", None) is not None for cs in statuses
    )


def pod_startup_failure(pod) -> str | None:
    """Failure reason during startup, or None while still waiting."""
    status = getattr(pod, "status", None)
    phase = getattr(status, "phase", None) or "Unknown"
    if phase in {"Failed", "Succeeded"}:
        reason = getattr(status, "reason", None) or "no reason reported"
        return f"pod phase {phase}: {reason}"
    for cs in getattr(status, "container_statuses", None) or []:
        waiting = getattr(getattr(cs, "state", None), "waiting", None)
        reason = getattr(waiting, "reason", None)
        if reason and reason not in _TRANSIENT_WAITING:
            return f"container {cs.name}: {reason}"
    return None


def _exit_code(resp) -> int:
    code = getattr(resp, "returncode", None)
    if code is None:
        return -1
    try:
        return int(code)
    except (TypeError, ValueError):
        return -1


# --- provider ----------------------------------------------------------------


class KubernetesSandboxProvider:
    """Concrete SandboxProvider backed by the Kubernetes API (plan 042)."""

    provider_name = "kubernetes"

    def __init__(
        self, namespace: str = "agent-platform", cluster_cidrs: tuple[str, ...] = ()
    ) -> None:
        self._namespace = namespace
        self._cluster_cidrs = tuple(cluster_cidrs)
        self._core_v1 = None
        self._networking_v1 = None
        self._timeout_probe: dict[str, bool] = {}

    # -- contract operations --------------------------------------------------

    async def create(self, spec: SandboxSpec) -> Sandbox:
        manifest = build_pod_manifest(spec, namespace=self._namespace)
        netpols = build_network_policies(spec, cluster_cidrs=self._cluster_cidrs)
        policy_names = [p["metadata"]["name"] for p in netpols]
        name = manifest["metadata"]["name"]

        def _create():
            from kubernetes.client.rest import ApiException

            created: list[str] = []
            try:
                for pol in netpols:
                    self._networking().create_namespaced_network_policy(
                        self._namespace, body=pol
                    )
                    created.append(pol["metadata"]["name"])
                self._core().create_namespaced_pod(self._namespace, body=manifest)
            except ApiException as exc:
                self._delete_policies_sync(created)
                if exc.status == 409:
                    raise ProviderError(
                        f"pod {name} already exists in namespace {self._namespace}"
                    ) from exc
                raise ProviderError(f"pod create failed: {exc}") from exc

        await asyncio.to_thread(_create)
        ready_timeout = float(spec.metadata.get("pod_ready_timeout", 60.0))
        pod = await self._wait_running(name, ready_timeout)
        logger.info(
            "kubernetes sandbox %s started as pod %s/%s",
            spec.sandbox_id,
            self._namespace,
            name,
        )

        sandbox = Sandbox(
            sandbox_id=spec.sandbox_id,
            provider=self.provider_name,
            image=spec.image,
            tenant_id=spec.tenant_id,
            user_id=spec.user_id,
            session_id=spec.session_id,
            workspace=spec.workspace,
            resources=spec.resources,
            network_policy=spec.network_policy,
            metadata={
                **spec.metadata,
                "pod_name": name,
                "namespace": self._namespace,
                "pod_uid": pod.metadata.uid,
                "network_policy_names": policy_names,
            },
        )
        sandbox.endpoints = resolve_endpoints(spec.ports, pod.status.pod_ip)
        return sandbox

    async def execute(self, sandbox: Sandbox, request: ExecutionRequest) -> ExecutionResult:
        pod = self._pod_of(sandbox)
        wrapper = await self._has_timeout_wrapper(sandbox)
        argv = build_exec_argv(
            request.command, request.cwd, request.env, request.timeout, wrapper
        )
        started = time.monotonic()
        try:
            stdout, stderr, exit_code = await asyncio.wait_for(
                asyncio.to_thread(
                    self._exec_blocking,
                    pod,
                    argv,
                    request.stdin.encode() if request.stdin else None,
                ),
                timeout=request.timeout + _TIMEOUT_GRACE,
            )
            # Same deadline heuristic as the docker provider: GNU coreutils
            # timeout exits 124; busybox wrapped with -s KILL propagates
            # 128+SIGKILL (137).
            elapsed = time.monotonic() - started
            timed_out = exit_code in (124, 137) and elapsed >= request.timeout * 0.9
        except asyncio.TimeoutError:
            stdout, stderr, exit_code, timed_out = b"", b"", -1, True

        limit = max(0, request.output_limit)
        truncated = len(stdout) > limit or len(stderr) > limit
        duration_ms = int((time.monotonic() - started) * 1000)
        return ExecutionResult(
            request_id=request.request_id,
            exit_code=exit_code,
            stdout=stdout[:limit].decode(errors="replace"),
            stderr=stderr[:limit].decode(errors="replace"),
            duration_ms=duration_ms,
            timed_out=timed_out,
            truncated=truncated,
            metadata={"pod_name": pod, "namespace": self._namespace},
        )

    async def upload_files(
        self, sandbox: Sandbox, files: list[FileUpload]
    ) -> list[FileUploadResult]:
        pod = self._pod_of(sandbox)
        mount = sandbox.workspace.mount_path or DEFAULT_MOUNT
        wrapper = await self._has_timeout_wrapper(sandbox)
        argv = build_exec_argv(
            ["tar", "-xf", "-", "-C", mount], cwd="/", timeout=_FILE_OP_TIMEOUT,
            wrapper_available=wrapper,
        )
        results: list[FileUploadResult] = []
        for file in files:
            try:
                data = upload_tar(file.path, file.content, mount)
                _out, err, code = await asyncio.wait_for(
                    asyncio.to_thread(self._exec_blocking, pod, argv, data),
                    timeout=_FILE_OP_TIMEOUT + _TIMEOUT_GRACE,
                )
                if code != 0:
                    results.append(
                        FileUploadResult(
                            path=file.path,
                            ok=False,
                            error=err.decode(errors="replace").strip() or f"exit code {code}",
                        )
                    )
                else:
                    results.append(FileUploadResult(path=file.path, ok=True))
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001 - per-file error reporting
                results.append(FileUploadResult(path=file.path, ok=False, error=str(exc)))
        return results

    async def download_files(
        self, sandbox: Sandbox, paths: list[str]
    ) -> list[FileDownloadResult]:
        pod = self._pod_of(sandbox)
        mount = sandbox.workspace.mount_path or DEFAULT_MOUNT
        results: list[FileDownloadResult] = []
        for path in paths:
            try:
                ensure_under_mount(path, mount)
                stdout, stderr = await asyncio.wait_for(
                    asyncio.to_thread(self._exec_collect, pod, download_argv(path, mount)),
                    timeout=_FILE_OP_TIMEOUT + _TIMEOUT_GRACE,
                )
                if not stdout.strip():
                    raise ProviderError(
                        stderr.strip() or f"no data returned for {path}"
                    )
                content = first_file_from_tar(decode_base64_output(stdout))
                if content is None:
                    results.append(FileDownloadResult(path=path, error="not a regular file"))
                else:
                    results.append(FileDownloadResult(path=path, content=content))
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001 - per-file error reporting
                results.append(FileDownloadResult(path=path, error=str(exc)))
        return results

    async def health_check(self, sandbox: Sandbox) -> HealthStatus:
        pod = sandbox.metadata.get("pod_name")
        if not pod:
            return HealthStatus(healthy=False, detail="sandbox has no kubernetes pod")
        try:
            pod_obj = await asyncio.to_thread(self._read_pod, pod)
            failure = pod_startup_failure(pod_obj)
            if failure:
                return HealthStatus(healthy=False, detail=failure)
            if not pod_ready(pod_obj):
                phase = getattr(pod_obj.status, "phase", None) or "Unknown"
                return HealthStatus(healthy=False, detail=f"pod phase: {phase}")
            mount = sandbox.workspace.mount_path or DEFAULT_MOUNT
            argv = build_exec_argv(
                f"test -d {shlex.quote(mount)} && test -w {shlex.quote(mount)}",
                cwd="/",
                timeout=10.0,
                wrapper_available=False,
            )
            _out, _err, code = await asyncio.to_thread(self._exec_blocking, pod, argv, None)
            if code != 0:
                return HealthStatus(healthy=False, detail=f"workspace probe exit code {code}")
            return HealthStatus(healthy=True, detail="pod running; workspace mounted")
        except SandboxError as exc:
            return HealthStatus(healthy=False, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - health probes report, never raise
            return HealthStatus(healthy=False, detail=f"health probe failed: {exc}")

    async def destroy(self, sandbox: Sandbox) -> None:
        pod = sandbox.metadata.get("pod_name")
        if not pod:
            return

        def _destroy():
            from kubernetes.client.rest import ApiException

            try:
                self._core().delete_namespaced_pod(
                    pod, self._namespace, grace_period_seconds=0
                )
            except ApiException as exc:
                if exc.status != 404:
                    raise ProviderError(f"pod delete failed: {exc}") from exc
            self._delete_policies_sync(
                sandbox.metadata.get("network_policy_names", [])
            )

        await asyncio.to_thread(_destroy)
        logger.info("kubernetes pod %s/%s removed (workspace retained)", self._namespace, pod)

    # -- internal -------------------------------------------------------------

    def _core(self):
        self._load()
        return self._core_v1

    def _networking(self):
        self._load()
        return self._networking_v1

    def _load(self):
        if self._core_v1 is None:
            try:
                from kubernetes import client, config
            except ImportError as exc:  # pragma: no cover - depends on env
                raise ProviderError(
                    "kubernetes SDK is not installed (pip install kubernetes)"
                ) from exc
            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            self._core_v1 = client.CoreV1Api()
            self._networking_v1 = client.NetworkingV1Api()

    def _pod_of(self, sandbox: Sandbox) -> str:
        pod = sandbox.metadata.get("pod_name")
        if not pod:
            raise SandboxUnavailable("sandbox has no kubernetes pod")
        return pod

    def _read_pod(self, pod: str):
        from kubernetes.client.rest import ApiException

        try:
            return self._core().read_namespaced_pod(pod, self._namespace)
        except ApiException as exc:
            if exc.status == 404:
                raise SandboxUnavailable(f"kubernetes pod missing: {pod}") from exc
            raise ProviderError(f"pod read failed: {exc}") from exc

    async def _wait_running(self, pod: str, timeout: float):
        core = self._core()
        deadline = time.monotonic() + max(1.0, timeout)
        while True:
            pod_obj = await asyncio.to_thread(core.read_namespaced_pod, pod, self._namespace)
            failure = pod_startup_failure(pod_obj)
            if failure:
                raise ProviderError(f"pod {pod} failed to start: {failure}")
            if pod_ready(pod_obj):
                return pod_obj
            if time.monotonic() >= deadline:
                phase = getattr(pod_obj.status, "phase", None) or "Unknown"
                raise ProviderError(
                    f"pod {pod} not ready after {timeout:.0f}s (phase: {phase})"
                )
            await asyncio.sleep(_POD_READY_POLL)

    def _exec_blocking(self, pod: str, argv: list[str], stdin_bytes: bytes | None):
        """Blocking exec start + drain. Runs inside a worker thread."""
        from kubernetes.stream import stream

        resp = stream(
            self._core().connect_get_namespaced_pod_exec,
            pod,
            self._namespace,
            command=argv,
            stderr=True,
            stdin=stdin_bytes is not None,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        out: list[str] = []
        err: list[str] = []
        try:
            if stdin_bytes:
                for i in range(0, len(stdin_bytes), _STDIN_CHUNK):
                    resp.write_stdin(bytes(stdin_bytes[i : i + _STDIN_CHUNK]))
                # Empty stdin frame signals EOF (apiserver websocket protocol).
                resp.write_stdin(b"")
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    out.append(resp.read_stdout())
                if resp.peek_stderr():
                    err.append(resp.read_stderr())
        finally:
            try:
                resp.close()
            except Exception:  # noqa: BLE001 - close is best-effort
                pass
        return "".join(out).encode(), "".join(err).encode(), _exit_code(resp)

    def _exec_collect(self, pod: str, argv: list[str]) -> tuple[str, str]:
        """Run an exec and return (stdout, stderr) as text."""
        out, err, _code = self._exec_blocking(pod, argv, None)
        return out.decode(errors="replace"), err.decode(errors="replace")

    async def _has_timeout_wrapper(self, sandbox: Sandbox) -> bool:
        pod = self._pod_of(sandbox)
        cached = self._timeout_probe.get(pod)
        if cached is not None:
            return cached
        try:
            _out, _err, code = await asyncio.to_thread(
                self._exec_blocking,
                pod,
                build_exec_argv("command -v timeout", cwd="/", wrapper_available=False),
                None,
            )
            available = code == 0
        except SandboxError:
            available = False
        self._timeout_probe[pod] = available
        return available

    def _delete_policies_sync(self, names) -> None:
        from kubernetes.client.rest import ApiException

        for name in names or []:
            try:
                self._networking().delete_namespaced_network_policy(name, self._namespace)
            except ApiException as exc:
                if exc.status != 404:
                    raise ProviderError(f"network policy delete failed: {exc}") from exc

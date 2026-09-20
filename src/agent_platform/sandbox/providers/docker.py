"""Docker SandboxProvider.

Maps the platform sandbox contract onto Docker Engine containers
(plan 041). Docker SDK is imported lazily inside methods so that the
sandbox core and the security-configuration unit tests do not require
the SDK or a running daemon.

Security posture applied on every container:

* non-root user (numeric ``65534:65534``; override via spec metadata
  ``container_user``)
* ``privileged=False``, all capabilities dropped, ``no-new-privileges``
* read-only root filesystem (override via metadata ``read_only_rootfs``)
  with a writable tmpfs at ``/tmp``
* CPU / memory / PIDs limits derived from ``SandboxResources``
* exactly one host bind mount: the persistent workspace, mapped to
  ``/workspace`` (never ``/``, ``/etc``, the Docker socket, or any
  credential path)
* only explicitly configured environment variables (host env is never
  inherited)
* Docker's default seccomp profile remains enabled; AppArmor is left to
  host configuration (where available)

Network mapping (documented limitation): ``NONE`` -> ``none``,
``INTERNAL`` -> dedicated internal network ``agent-platform-internal``
(must be pre-created), ``ALLOWLIST``/``INTERNET_ONLY``/``FULL`` ->
default bridge. True egress filtering/allowlisting requires a network
proxy and is out of scope for this phase.

Timeout: commands are wrapped with ``timeout -s KILL <seconds>`` inside
the container (probed once per container; present in GNU coreutils and
busybox images) with a client-side deadline as backstop. When the GNU
wrapper is unavailable and the process outlives the client deadline the
exec is abandoned (daemon thread) and destroyed with the container.
"""

import asyncio
import io
import logging
import math
import posixpath
import shlex
import socket as socket_module
import tarfile
import time
from pathlib import Path, PurePosixPath

from agent_platform.sandbox.errors import (
    PermissionDenied,
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
    PortSpec,
    Sandbox,
    SandboxSpec,
    WorkspaceMount,
)
from agent_platform.sandbox.policy import is_path_under, parse_cpu, parse_memory

logger = logging.getLogger(__name__)

DEFAULT_USER = "65534:65534"  # numeric "nobody": non-root on virtually any image
INTERNAL_NETWORK = "agent-platform-internal"
_LABEL_PREFIX = "agent_platform.sandbox"
_DEFAULT_MOUNT = "/workspace"
_HOLD_COMMAND = ["tail", "-f", "/dev/null"]
_TIMEOUT_GRACE = 5.0

# Workspace bind targets that must never be used (spec section 5: never
# mount /, /etc, /var/run/docker.sock, credentials, ...).
_FORBIDDEN_MOUNT_ROOTS = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/var/run",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/boot",
)


# --- pure helpers (unit-testable without docker SDK or daemon) ---------------


def container_name(sandbox_id: str) -> str:
    return f"agent-platform-sbx-{sandbox_id}"


def network_mode_for(mode: NetworkMode) -> str:
    if mode is NetworkMode.NONE:
        return "none"
    if mode is NetworkMode.INTERNAL:
        return INTERNAL_NETWORK
    # ALLOWLIST requires an egress proxy (plan 041 section 16: out of scope).
    return "bridge"


def published_ports(ports: tuple[PortSpec, ...]) -> dict[str, tuple[str, None]]:
    """Loopback-bound ephemeral publish config for declared ports (spec 9.2).

    ``("127.0.0.1", None)`` -> docker picks a free host port and binds it
    to the loopback interface only; publishing to ``0.0.0.0`` is
    prohibited. Undeclared ports are never published.
    """
    return {f"{port.container_port}/tcp": ("127.0.0.1", None) for port in ports}


def resolve_endpoints(
    ports: tuple[PortSpec, ...], network_ports: dict | None
) -> list[Endpoint]:
    """Map declared ports onto docker's NetworkSettings.Ports report.

    A declared port without a resolved host binding is a provider bug
    (spec section 9.2) and fails creation instead of yielding a READY
    sandbox the platform cannot reach.
    """
    network_ports = network_ports or {}
    endpoints: list[Endpoint] = []
    for port in ports:
        bindings = network_ports.get(f"{port.container_port}/tcp") or []
        if not bindings:
            raise ProviderError(
                f"declared port {port.name!r} ({port.container_port}/tcp) "
                "was not published by the docker daemon"
            )
        binding = bindings[0]
        host_ip = binding.get("HostIp") or "127.0.0.1"
        host_port = binding.get("HostPort")
        if not host_port:
            raise ProviderError(
                f"declared port {port.name!r} has no resolved host port"
            )
        endpoints.append(Endpoint(name=port.name, address=f"{host_ip}:{host_port}"))
    return endpoints


def build_container_config(spec: SandboxSpec, workspace_root: str) -> dict:
    """Pure mapping SandboxSpec -> docker ``containers.run`` kwargs."""
    workspace = spec.workspace or WorkspaceMount(workspace_id="")
    if not workspace.workspace_id:
        raise ProviderError("docker provider requires spec.workspace.workspace_id")
    mount_path = workspace.mount_path or _DEFAULT_MOUNT
    normalized_mount = posixpath.normpath(mount_path)
    if normalized_mount == "/" or any(
        normalized_mount == root or normalized_mount.startswith(root + "/")
        for root in _FORBIDDEN_MOUNT_ROOTS
    ):
        raise ProviderError(f"unsafe workspace mount path: {mount_path}")

    host_dir = str(Path(workspace_root) / workspace.workspace_id)
    user = spec.metadata.get("container_user", DEFAULT_USER)
    read_only = bool(spec.metadata.get("read_only_rootfs", True))
    tmpfs_size = spec.metadata.get("tmpfs_size", "256m")

    config = {
        "name": container_name(spec.sandbox_id),
        "user": user,
        "privileged": False,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": read_only,
        "tmpfs": {"/tmp": f"size={tmpfs_size}"},
        "volumes": {host_dir: {"bind": mount_path, "mode": "rw"}},
        "environment": dict(spec.env),  # explicit only; never the host environment
        "network_mode": network_mode_for(spec.network_policy.mode),
        "mem_limit": parse_memory(spec.resources.memory),
        "nano_cpus": int(parse_cpu(spec.resources.cpu) * 1e9),
        "pids_limit": spec.resources.pids,
        "labels": {
            f"{_LABEL_PREFIX}.sandbox_id": spec.sandbox_id,
            f"{_LABEL_PREFIX}.tenant_id": spec.tenant_id,
            f"{_LABEL_PREFIX}.user_id": spec.user_id,
            f"{_LABEL_PREFIX}.session_id": spec.session_id,
            f"{_LABEL_PREFIX}.managed": "true",
        },
    }
    if spec.ports:
        config["ports"] = published_ports(spec.ports)
    return config


def command_argv(command: str | list[str], timeout: float, wrapper_available: bool) -> list[str]:
    """Normalize the request command to an exec argv.

    String commands run through ``/bin/sh -c`` (never concatenated into a
    larger string). When the GNU/busybox ``timeout`` wrapper is available
    it is prepended so the process is terminated inside the container.
    """
    wrapper = ["timeout", "-s", "KILL", str(max(1, math.ceil(timeout)))] if wrapper_available else []
    if isinstance(command, str):
        return [*wrapper, "/bin/sh", "-c", command]
    if not command:
        raise ProviderError("empty command")
    return [*wrapper, *command]


def ensure_under_mount(path: str, mount_path: str) -> None:
    """Reject any path outside the workspace mount (defense in depth)."""
    normalized = posixpath.normpath(path)
    if not posixpath.isabs(normalized) or not is_path_under(normalized, mount_path):
        raise PermissionDenied(f"path outside workspace mount {mount_path}: {path}")


def upload_tar(path: str, content: bytes, mount_path: str) -> bytes:
    """Build a docker put_archive tar (relative to mount_path) for one file.

    Implicit parent directories are included so nested targets work like
    ``docker cp``.
    """
    ensure_under_mount(path, mount_path)
    rel = PurePosixPath(posixpath.relpath(posixpath.normpath(path), mount_path))
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        parents = list(rel.parents)
        for parent in reversed(parents[:-1] if parents else []):
            info = tarfile.TarInfo(str(parent))
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tar.addfile(info)
        info = tarfile.TarInfo(str(rel))
        info.size = len(content)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def first_file_from_tar(data: bytes) -> bytes | None:
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    handle = tar.extractfile(member)
                    if handle is not None:
                        return handle.read()
    except tarfile.TarError:
        return None
    return None


def demux_socket_stream(sock, stdin_text: str | None) -> tuple[bytes, bytes]:
    """Read a docker raw (tty=False) multiplexed exec stream until EOF.

    Frame format: 1 byte stream type, 3 padding bytes, 4 byte big-endian
    payload size. Used only for the stdin path; the plain path uses the
    SDK's ``demux=True``.
    """
    if stdin_text:
        raw = getattr(sock, "_sock", sock)
        send = getattr(sock, "sendall", None) or raw.sendall
        try:
            send(stdin_text.encode())
            raw.shutdown(socket_module.SHUT_WR)
        except OSError as exc:
            raise ProviderError(f"failed to write stdin to exec stream: {exc}") from exc

    stdout, stderr = bytearray(), bytearray()
    recv = getattr(sock, "recv", None)
    if recv is None:
        recv = getattr(getattr(sock, "_sock", sock), "recv", None)
    if recv is None:
        recv = lambda n: sock.read(n) or b""  # noqa: E731 - SocketIO fallback

    def read_exact(n: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < n:
            try:
                chunk = recv(min(4096, n - len(chunks)))
            except OSError:
                break
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    while True:
        header = read_exact(8)
        if len(header) < 8:
            break
        frame_type = header[0]
        size = int.from_bytes(header[4:8], "big")
        if size == 0:
            continue
        payload = read_exact(size)
        if frame_type == 1:
            stdout.extend(payload)
        elif frame_type == 2:
            stderr.extend(payload)
        if len(payload) < size:
            break
    return bytes(stdout), bytes(stderr)


# --- provider ----------------------------------------------------------------


class DockerSandboxProvider:
    """Concrete SandboxProvider backed by the Docker Engine."""

    provider_name = "docker"

    def __init__(self, workspace_root: str = ".agent-platform/workspaces") -> None:
        self._workspace_root = workspace_root
        self._client = None
        self._timeout_probe: dict[str, bool] = {}

    # -- contract operations --------------------------------------------------

    async def create(self, spec: SandboxSpec) -> Sandbox:
        import docker

        config = build_container_config(spec, self._workspace_root)
        host_dir = Path(self._workspace_root) / spec.workspace.workspace_id
        host_dir.mkdir(parents=True, exist_ok=True)
        hold = spec.metadata.get("hold_command", _HOLD_COMMAND)

        def _run():
            try:
                return self._docker().containers.run(spec.image, hold, detach=True, **config)
            except docker.errors.ImageNotFound as exc:
                raise ProviderError(f"image not found: {spec.image}") from exc
            except docker.errors.APIError as exc:
                raise ProviderError(f"container create failed: {exc}") from exc

        container = await asyncio.to_thread(_run)
        logger.info("docker sandbox %s started as container %s", spec.sandbox_id, container.id[:12])
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
                "container_id": container.id,
                "container_name": config["name"],
            },
        )
        if spec.ports:
            # Endpoint resolution is part of create() (spec section 9.2):
            # a READY sandbox must carry an address per declared port.
            await asyncio.to_thread(container.reload)
            sandbox.endpoints = resolve_endpoints(
                spec.ports, container.attrs.get("NetworkSettings", {}).get("Ports")
            )
        return sandbox

    async def execute(self, sandbox: Sandbox, request: ExecutionRequest) -> ExecutionResult:
        container = await asyncio.to_thread(self._get_container, sandbox)
        wrapper = await self._has_timeout_wrapper(container)
        argv = command_argv(request.command, request.timeout, wrapper)
        user = sandbox.metadata.get("container_user", DEFAULT_USER)
        exec_id = await asyncio.to_thread(
            self._exec_create, container, argv, request.cwd, request.env, user
        )
        started = time.monotonic()
        try:
            stdout, stderr, exit_code = await asyncio.wait_for(
                asyncio.to_thread(self._exec_run_blocking, container, exec_id, request.stdin),
                timeout=request.timeout + _TIMEOUT_GRACE,
            )
            # Deadline detection: GNU coreutils timeout exits 124 when the
            # deadline elapses; busybox (alpine images) propagates
            # 128+signal instead -- we wrap with -s KILL, i.e. 137. The
            # elapsed check avoids treating a command that legitimately
            # exits with one of these codes as a timeout.
            elapsed = time.monotonic() - started
            timed_out = (
                exit_code in (124, 137) and elapsed >= request.timeout * 0.9
            )
        except asyncio.TimeoutError:
            # GNU wrapper missing or stuck: abandon the exec; the daemon
            # thread ends when the process exits or the container dies.
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
            metadata={"container_id": container.id, "exec_id": exec_id},
        )

    async def upload_files(self, sandbox: Sandbox, files: list[FileUpload]) -> list[FileUploadResult]:
        container = await asyncio.to_thread(self._get_container, sandbox)
        mount = sandbox.workspace.mount_path or _DEFAULT_MOUNT
        results: list[FileUploadResult] = []
        for file in files:
            try:
                data = upload_tar(file.path, file.content, mount)
                await asyncio.to_thread(lambda d=data: container.put_archive(mount, d))
                results.append(FileUploadResult(path=file.path, ok=True))
            except SandboxError:
                raise
            except Exception as exc:  # noqa: BLE001 - per-file error reporting
                results.append(FileUploadResult(path=file.path, ok=False, error=str(exc)))
        return results

    async def download_files(self, sandbox: Sandbox, paths: list[str]) -> list[FileDownloadResult]:
        container = await asyncio.to_thread(self._get_container, sandbox)
        mount = sandbox.workspace.mount_path or _DEFAULT_MOUNT
        results: list[FileDownloadResult] = []
        for path in paths:
            ensure_under_mount(path, mount)
            try:
                chunks, _stat = await asyncio.to_thread(lambda p=path: container.get_archive(p))
                content = first_file_from_tar(b"".join(chunks))
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
        try:
            container = await asyncio.to_thread(self._get_container, sandbox)
        except SandboxUnavailable as exc:
            return HealthStatus(healthy=False, detail=str(exc))
        try:
            await asyncio.to_thread(container.reload)
            if container.status != "running":
                return HealthStatus(healthy=False, detail=f"container status: {container.status}")
            mount = sandbox.workspace.mount_path or _DEFAULT_MOUNT
            probe = f"test -d {shlex.quote(mount)} && test -w {shlex.quote(mount)}"
            exec_id = await asyncio.to_thread(
                self._exec_create, container, ["/bin/sh", "-c", probe], "/", {}, None
            )
            _out, _err, code = await asyncio.to_thread(self._exec_run_blocking, container, exec_id, "")
            if code != 0:
                return HealthStatus(healthy=False, detail=f"workspace probe exit code {code}")
            return HealthStatus(healthy=True, detail="container running; workspace mounted")
        except SandboxError:
            raise
        except Exception as exc:  # noqa: BLE001 - health probes report, never raise
            return HealthStatus(healthy=False, detail=f"health probe failed: {exc}")

    async def destroy(self, sandbox: Sandbox) -> None:
        container_id = sandbox.metadata.get("container_id")
        self._timeout_probe.pop(container_id, None)
        if not container_id:
            return
        import docker

        def _remove():
            try:
                self._docker().containers.get(container_id).remove(force=True)
            except docker.errors.NotFound:
                pass  # idempotent destroy

        await asyncio.to_thread(_remove)
        logger.info("docker container %s removed (workspace retained)", container_id[:12])

    async def destroy_workspace(self, workspace: WorkspaceMount) -> None:
        """Remove the host workspace directory (warm-pool spec section 7.4).

        Optional capability used by the warm-pool maintainer when evicting
        a *never-claimed* sandbox: pool members provably hold no session
        data, so their workspace directory can be deleted instead of
        retained. Must never be called for a claimed sandbox (normal
        destroy keeps the workspace).
        """
        workspace_id = workspace.workspace_id if workspace else ""
        if not workspace_id:
            return
        host_dir = Path(self._workspace_root) / workspace_id

        def _rmtree():
            import shutil

            shutil.rmtree(host_dir, ignore_errors=True)  # tolerate missing dir

        await asyncio.to_thread(_rmtree)
        logger.info("docker workspace directory %s removed", host_dir)

    # -- internal -------------------------------------------------------------

    def _docker(self):
        if self._client is None:
            try:
                import docker
            except ImportError as exc:  # pragma: no cover - depends on env
                raise ProviderError(
                    "docker SDK is not installed (pip install docker)"
                ) from exc
            self._client = docker.from_env()
        return self._client

    def _get_container(self, sandbox: Sandbox):
        import docker

        container_id = sandbox.metadata.get("container_id")
        if not container_id:
            raise SandboxUnavailable("sandbox has no docker container")
        try:
            return self._docker().containers.get(container_id)
        except docker.errors.NotFound as exc:
            raise SandboxUnavailable(f"docker container missing: {container_id}") from exc

    @staticmethod
    def _exec_create(container, argv: list[str], workdir: str, env: dict, user: str | None) -> str:
        import docker

        try:
            payload = container.client.api.exec_create(
                container.id,
                cmd=argv,
                stdout=True,
                stderr=True,
                workdir=workdir or None,
                environment=env or None,
                user=user or None,
            )
            return payload["Id"]
        except docker.errors.NotFound as exc:
            raise SandboxUnavailable(f"container gone during exec: {exc}") from exc
        except docker.errors.APIError as exc:
            raise ProviderError(f"exec create failed: {exc}") from exc

    @staticmethod
    def _exec_run_blocking(container, exec_id: str, stdin_text: str):
        """Blocking exec start + inspect. Runs inside a worker thread."""
        import docker

        api = container.client.api
        try:
            if stdin_text:
                sock = api.exec_start(exec_id, socket=True, tty=False)
                stdout, stderr = demux_socket_stream(sock, stdin_text)
            else:
                output = api.exec_start(exec_id, tty=False, demux=True)
                out, err = output if output else (b"", b"")
                stdout, stderr = out or b"", err or b""
            inspect = api.exec_inspect(exec_id)
            exit_code = int(inspect.get("ExitCode", -1))
            return stdout, stderr, exit_code
        except docker.errors.APIError as exc:
            raise ProviderError(f"exec run failed: {exc}") from exc

    async def _has_timeout_wrapper(self, container) -> bool:
        cached = self._timeout_probe.get(container.id)
        if cached is not None:
            return cached
        try:
            exec_id = await asyncio.to_thread(
                self._exec_create, container, ["/bin/sh", "-c", "command -v timeout"], "/", {}, None
            )
            _out, _err, code = await asyncio.to_thread(
                self._exec_run_blocking, container, exec_id, ""
            )
            available = code == 0
        except SandboxError:
            available = False
        self._timeout_probe[container.id] = available
        return available

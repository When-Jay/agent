"""Docker sandbox provider tests.

Two layers:

1. Security/configuration unit tests against the pure helpers
   (build_container_config, command_argv, upload_tar, ...) — no docker
   SDK interaction, no daemon required.
2. Docker-daemon contract tests (full SandboxProvider contract suite,
   timeout enforcement, workspace persistence) — skipped when the
   Docker daemon is not reachable.
"""

import asyncio
import io
import os
import tarfile

import pytest

from agent_platform.sandbox.errors import PermissionDenied, ProviderError
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import (
    ExecutionRequest,
    FileUpload,
    NetworkMode,
    SandboxResources,
    SandboxSpec,
    WorkspaceMount,
)
from agent_platform.sandbox.policy import is_path_under
from agent_platform.sandbox.providers.docker import (
    DockerSandboxProvider,
    build_container_config,
    command_argv,
    container_name,
    demux_socket_stream,
    ensure_under_mount,
    first_file_from_tar,
    network_mode_for,
    upload_tar,
)
from agent_platform.sandbox.registry import SandboxProviderRegistry
from sandbox_contract import run_provider_contract_suite

ALPINE = "alpine:3.20"

_DOCKER_STATE: bool | None = None


def _docker_available() -> bool:
    global _DOCKER_STATE
    if _DOCKER_STATE is None:
        try:
            import docker

            docker.from_env().ping()
            _DOCKER_STATE = True
        except Exception:  # noqa: BLE001 - any failure means "skip"
            _DOCKER_STATE = False
    return _DOCKER_STATE


def _spec(
    image: str = "platform/test:latest",
    mount: str = "/workspace",
    workspace_id: str = "ws-1",
    **kwargs,
) -> SandboxSpec:
    return SandboxSpec(
        image=image,
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        workspace=WorkspaceMount(workspace_id=workspace_id, mount_path=mount),
        **kwargs,
    )


def _config(**kwargs) -> dict:
    return build_container_config(_spec(**kwargs), workspace_root="/srv/workspaces")


# --- security posture (plan 041 section 4/5/9) -------------------------------


def test_container_config_security_posture():
    config = _config()
    assert config["user"] == "65534:65534"  # non-root
    assert config["privileged"] is False
    assert config["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in config["security_opt"]
    assert config["read_only"] is True
    assert "/tmp" in config["tmpfs"]


def test_container_config_resource_limits():
    config = _config(resources=SandboxResources(cpu="500m", memory="512Mi", pids=64))
    assert config["nano_cpus"] == 500_000_000
    assert config["mem_limit"] == 512 * 2**20
    assert config["pids_limit"] == 64


def test_container_config_mounts_only_the_workspace():
    config = _config(workspace_id="ws-42")
    volumes = config["volumes"]
    assert list(volumes) == [os.path.normpath("/srv/workspaces/ws-42")]
    (host, options) = next(iter(volumes.items()))
    assert options == {"bind": "/workspace", "mode": "rw"}
    serialized = str(config)
    assert "docker.sock" not in serialized
    assert host != "/" and "/etc" not in host


@pytest.mark.parametrize(
    "mount",
    ["/", "/etc", "/etc/passwd", "/var/run", "/var/run/docker.sock", "/proc", "/sys"],
)
def test_container_config_rejects_dangerous_mount_targets(mount):
    with pytest.raises(ProviderError):
        _config(mount=mount)


def test_container_config_never_inherits_host_env(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", "tcp://attacker:2375")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak")
    config = _config()
    assert config["environment"] == {}


def test_container_config_explicit_env_only():
    config = _config(env={"FOO": "bar"})
    assert config["environment"] == {"FOO": "bar"}


def test_container_config_metadata_overrides():
    config = _config(metadata={"container_user": "0:0", "read_only_rootfs": False})
    assert config["user"] == "0:0"
    assert config["read_only"] is False


def test_container_config_labels_identify_sandbox():
    config = _config(sandbox_id="sbx-1")
    labels = config["labels"]
    assert labels["agent_platform.sandbox.sandbox_id"] == "sbx-1"
    assert labels["agent_platform.sandbox.tenant_id"] == "tenant-1"
    assert labels["agent_platform.sandbox.managed"] == "true"


def test_container_config_requires_workspace():
    spec = _spec(workspace_id="")
    with pytest.raises(ProviderError):
        build_container_config(spec, workspace_root="/srv/workspaces")


def test_container_name_is_deterministic():
    assert container_name("abc") == "agent-platform-sbx-abc"
    assert container_name("abc") == container_name("abc")


@pytest.mark.parametrize(
    "mode,expected",
    [
        (NetworkMode.NONE, "none"),
        (NetworkMode.INTERNAL, "agent-platform-internal"),
        (NetworkMode.ALLOWLIST, "bridge"),
        (NetworkMode.INTERNET_ONLY, "bridge"),
        (NetworkMode.FULL, "bridge"),
    ],
)
def test_network_mode_mapping(mode, expected):
    assert network_mode_for(mode) == expected


# --- command / path / tar helpers --------------------------------------------


def test_command_argv_wraps_with_timeout():
    argv = command_argv("echo hi", timeout=2.4, wrapper_available=True)
    assert argv == ["timeout", "-s", "KILL", "3", "/bin/sh", "-c", "echo hi"]


def test_command_argv_without_wrapper_or_list_form():
    assert command_argv("echo hi", 5, wrapper_available=False) == ["/bin/sh", "-c", "echo hi"]
    assert command_argv(["echo", "hi"], 5, wrapper_available=True) == [
        "timeout",
        "-s",
        "KILL",
        "5",
        "echo",
        "hi",
    ]
    assert command_argv(["echo", "hi"], 5, wrapper_available=False) == ["echo", "hi"]


def test_ensure_under_mount_rejects_outside_and_traversal():
    ensure_under_mount("/workspace/a.txt", "/workspace")
    with pytest.raises(PermissionDenied):
        ensure_under_mount("/etc/passwd", "/workspace")
    with pytest.raises(PermissionDenied):
        ensure_under_mount("/workspace/../etc/passwd", "/workspace")
    with pytest.raises(PermissionDenied):
        ensure_under_mount("relative/path", "/workspace")
    with pytest.raises(PermissionDenied):
        ensure_under_mount("/workspace_evil/x", "/workspace")


def test_policy_is_path_under_survives_traversal():
    # regression guard: "../" must be normalized before the containment check
    assert not is_path_under("/workspace/../etc", "/workspace")
    assert is_path_under("/workspace/sub/../a", "/workspace")


def test_upload_tar_is_relative_to_mount_and_creates_parents():
    data = upload_tar("/workspace/output/a.txt", b"data", "/workspace")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        names = {m.name for m in tar.getmembers()}
        handle = tar.extractfile("output/a.txt")
        assert handle is not None and handle.read() == b"data"
    assert names == {"output", "output/a.txt"}


def test_upload_tar_rejects_outside_mount():
    with pytest.raises(PermissionDenied):
        upload_tar("/etc/passwd", b"x", "/workspace")


def test_first_file_from_tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    assert first_file_from_tar(buf.getvalue()) == b"abc"
    assert first_file_from_tar(b"not-a-tar") is None


class _FakeExecSocket:
    def __init__(self, frames: bytes) -> None:
        self.frames = frames
        self.written = b""
        self.shutdown_called = False

    def sendall(self, data: bytes) -> None:
        self.written += data

    def _shutdown_writer(self) -> None:
        self.shutdown_called = True

    def recv(self, n: int) -> bytes:
        if not self.frames:
            return b""
        chunk, self.frames = self.frames[:n], self.frames[n:]
        return chunk


def test_demux_socket_stream_splits_stdout_stderr_and_sends_stdin():
    frames = (
        b"\x01\x00\x00\x00\x00\x00\x00\x05hello"
        b"\x02\x00\x00\x00\x00\x00\x00\x04oops"
    )
    sock = _FakeExecSocket(frames)
    sock._sock = type("_S", (), {"shutdown": lambda self, how: sock._shutdown_writer()})()
    stdout, stderr = demux_socket_stream(sock, "ping\n")
    assert stdout == b"hello"
    assert stderr == b"oops"
    assert sock.written == b"ping\n"
    assert sock.shutdown_called


# --- daemon-backed contract tests (skipped without Docker) -------------------


def _docker_manager(workspace_root) -> SandboxManager:
    registry = SandboxProviderRegistry()
    registry.register("docker", DockerSandboxProvider(workspace_root=str(workspace_root)))
    return SandboxManager(registry, default_provider="docker")


def _docker_spec(workspace_id: str = "ws-contract") -> SandboxSpec:
    spec = _spec(image=ALPINE, workspace_id=workspace_id)
    return spec


def _docker_spec_with_ports(workspace_id: str) -> SandboxSpec:
    from agent_platform.sandbox.models import PortSpec

    return _spec(
        image=ALPINE,
        workspace_id=workspace_id,
        ports=(PortSpec(name="mcp", container_port=8080),),
    )


def test_container_config_publishes_declared_ports_loopback():
    config = build_container_config(
        _docker_spec_with_ports("ws-ports"), ".agent-platform/workspaces"
    )
    assert config["ports"] == {"8080/tcp": ("127.0.0.1", None)}


def test_container_config_without_ports_publishes_nothing():
    config = build_container_config(_docker_spec("ws-noports"), ".agent-platform/workspaces")
    assert "ports" not in config


def test_docker_create_resolves_endpoints_for_declared_ports(tmp_path):
    _require_docker()

    async def scenario():
        manager = _docker_manager(tmp_path / "ws")
        sandbox = await manager.create(_docker_spec_with_ports("ws-endpoints"))
        assert sandbox.status.value == "ready"
        assert len(sandbox.endpoints) == 1
        endpoint = sandbox.endpoints[0]
        assert endpoint.name == "mcp"
        host, port = endpoint.address.split(":")
        assert host == "127.0.0.1"
        assert int(port) > 0
        await manager.destroy(sandbox.sandbox_id)

    asyncio.run(scenario())


def _require_docker():
    if not _docker_available():
        pytest.skip("Docker daemon not available")


def test_docker_provider_passes_full_contract_suite(tmp_path):
    _require_docker()
    run_provider_contract_suite(
        lambda: _docker_manager(tmp_path / "ws"),
        spec=_docker_spec(),
    )


def test_docker_execute_enforces_timeout(tmp_path):
    _require_docker()

    async def scenario():
        manager = _docker_manager(tmp_path / "ws")
        sandbox = await manager.create(_docker_spec("ws-timeout"))
        result = await manager.execute(
            sandbox.sandbox_id,
            ExecutionRequest(request_id="slow", command="sleep 5", timeout=1.0),
        )
        assert result.timed_out is True
        # GNU coreutils timeout exits 124 on deadline; busybox (alpine)
        # propagates 128+SIGKILL because the wrapper uses -s KILL.
        assert result.exit_code in (124, 137)
        await manager.destroy(sandbox.sandbox_id)

    asyncio.run(scenario())


def test_docker_workspace_persists_across_destroy(tmp_path):
    _require_docker()

    async def scenario():
        root = tmp_path / "ws"
        manager = _docker_manager(root)
        sandbox = await manager.create(_docker_spec("ws-persist"))
        await manager.upload(
            sandbox.sandbox_id, [FileUpload(path="/workspace/keep.txt", content=b"persist-me")]
        )
        await manager.destroy(sandbox.sandbox_id)

        # A brand-new sandbox mounting the same workspace sees the data.
        manager2 = _docker_manager(root)
        sandbox2 = await manager2.create(_docker_spec("ws-persist"))
        assert sandbox2.sandbox_id != sandbox.sandbox_id
        downloads = await manager2.download(sandbox2.sandbox_id, ["/workspace/keep.txt"])
        assert downloads[0].content == b"persist-me"
        await manager2.destroy(sandbox2.sandbox_id)

    asyncio.run(scenario())

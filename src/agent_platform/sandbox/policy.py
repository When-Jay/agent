"""Sandbox policy validation.

Policy is evaluated by the SandboxManager before sandbox creation and
before every execution/file operation.
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
import posixpath
import re

from agent_platform.sandbox.errors import PermissionDenied, ResourceLimitExceeded
from agent_platform.sandbox.models import ExecutionRequest, NetworkMode, SandboxSpec


@dataclass(frozen=True)
class SandboxPolicy:
    """Platform-side limits. None/empty fields mean no restriction."""

    allowed_images: tuple[str, ...] = ()
    max_cpu: str | None = None
    max_memory: str | None = None
    max_timeout: float | None = None
    max_output: int | None = None
    allowed_network_mode: NetworkMode | None = None
    allowed_workspace_root: str | None = None


def parse_cpu(value: str) -> float:
    """Parse a k8s-style CPU quantity: "1" -> 1.0, "500m" -> 0.5."""
    text = value.strip()
    if text.endswith("m"):
        return float(text[:-1]) / 1000
    return float(text)


_MEMORY_UNITS = {
    "Ki": 2**10,
    "Mi": 2**20,
    "Gi": 2**30,
    "Ti": 2**40,
    "K": 10**3,
    "M": 10**6,
    "G": 10**9,
    "T": 10**12,
}


def parse_memory(value: str) -> int:
    """Parse a k8s-style memory quantity: "2Gi" -> bytes."""
    text = value.strip()
    for suffix, multiplier in _MEMORY_UNITS.items():
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * multiplier)
    return int(float(text))


def is_path_under(path: str, root: str) -> bool:
    candidate = PurePosixPath(posixpath.normpath(path))
    boundary = PurePosixPath(posixpath.normpath(root))
    return candidate == boundary or boundary in candidate.parents


class PolicyValidator:
    def validate_spec(self, policy: SandboxPolicy, spec: SandboxSpec) -> None:
        if policy.allowed_images and spec.image not in policy.allowed_images:
            raise PermissionDenied(f"image not allowed: {spec.image}")
        if policy.max_cpu is not None and parse_cpu(spec.resources.cpu) > parse_cpu(policy.max_cpu):
            raise ResourceLimitExceeded(f"cpu {spec.resources.cpu} exceeds limit {policy.max_cpu}")
        if policy.max_memory is not None and parse_memory(spec.resources.memory) > parse_memory(
            policy.max_memory
        ):
            raise ResourceLimitExceeded(
                f"memory {spec.resources.memory} exceeds limit {policy.max_memory}"
            )
        if policy.allowed_network_mode is not None and spec.network_policy.mode is not (
            policy.allowed_network_mode
        ):
            raise PermissionDenied(
                f"network mode {spec.network_policy.mode.value} is not allowed"
            )
        self._validate_ports(spec)

    def _validate_ports(self, spec: SandboxSpec) -> None:
        """Port exposure rules (sandbox-spec.md sections 9.1-9.2)."""
        if not spec.ports:
            return
        if spec.network_policy.mode is NetworkMode.NONE:
            # Nothing is reachable in NONE mode; declaring ports there is
            # a specification error.
            raise PermissionDenied(
                "declared ports require a reachable network mode; NONE publishes nothing"
            )
        names: set[str] = set()
        for port in spec.ports:
            if not isinstance(port.container_port, int) or not 1 <= port.container_port <= 65535:
                raise PermissionDenied(
                    f"declared port {port.name!r} has invalid container_port "
                    f"{port.container_port!r}"
                )
            if not port.name or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", port.name):
                raise PermissionDenied(
                    f"declared port name {port.name!r} must be a lowercase "
                    "dns-style label ([a-z][a-z0-9-]*)"
                )
            if port.name in names:
                raise PermissionDenied(f"declared port name is not unique: {port.name!r}")
            names.add(port.name)

    def validate_execution(self, policy: SandboxPolicy, request: ExecutionRequest) -> None:
        if policy.max_timeout is not None and request.timeout > policy.max_timeout:
            raise ResourceLimitExceeded(f"timeout {request.timeout} exceeds limit {policy.max_timeout}")
        if policy.max_output is not None and request.output_limit > policy.max_output:
            raise ResourceLimitExceeded(
                f"output_limit {request.output_limit} exceeds limit {policy.max_output}"
            )
        if policy.allowed_workspace_root is not None and not is_path_under(
            request.cwd, policy.allowed_workspace_root
        ):
            raise PermissionDenied(f"cwd outside allowed workspace: {request.cwd}")

    def validate_paths(self, policy: SandboxPolicy, paths: list[str]) -> None:
        if policy.allowed_workspace_root is None:
            return
        for path in paths:
            if not is_path_under(path, policy.allowed_workspace_root):
                raise PermissionDenied(f"path outside allowed workspace: {path}")

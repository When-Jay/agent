"""Kubernetes sandbox provider tests (plan 042).

Two layers:

1. Security/configuration unit tests against the pure helpers
   (build_pod_manifest, build_network_policies, build_exec_argv, ...) —
   no cluster required. Startup-state helpers use the kubernetes SDK's
   model classes (the SDK is a regular dependency).
2. Cluster contract tests (full SandboxProvider contract suite, timeout
   enforcement) — skipped when no Kubernetes cluster is reachable.
"""

import asyncio

import pytest

from agent_platform.sandbox.errors import PermissionDenied, ProviderError
from agent_platform.sandbox.manager import SandboxManager
from agent_platform.sandbox.models import (
    ExecutionRequest,
    NetworkMode,
    PortSpec,
    SandboxSpec,
    WorkspaceMount,
)
from agent_platform.sandbox.providers.kubernetes import (
    KubernetesSandboxProvider,
    build_exec_argv,
    build_network_policies,
    build_pod_manifest,
    container_resources,
    decode_base64_output,
    download_argv,
    parse_user,
    pod_name,
    pod_ready,
    pod_startup_failure,
    resolve_endpoints,
    sanitize_label_value,
    workspace_volume,
)
from agent_platform.sandbox.registry import SandboxProviderRegistry
from sandbox_contract import run_provider_contract_suite

ALPINE = "alpine:3.20"
NAMESPACE = "agent-platform"


def _spec(**kwargs) -> SandboxSpec:
    return SandboxSpec(
        image=kwargs.pop("image", "platform/test:latest"),
        tenant_id=kwargs.pop("tenant_id", "tenant-1"),
        user_id=kwargs.pop("user_id", "user-1"),
        session_id=kwargs.pop("session_id", "session-1"),
        workspace=WorkspaceMount(
            workspace_id=kwargs.pop("workspace_id", "ws-1"),
            mount_path=kwargs.pop("mount_path", "/workspace"),
        ),
        **kwargs,
    )


def _manifest(**kwargs) -> dict:
    return build_pod_manifest(_spec(sandbox_id="sbx-1", **kwargs), namespace=NAMESPACE)


# --- security posture (plan 042 sections 5/6) ---------------------------------


def test_pod_manifest_security_posture():
    manifest = _manifest()
    pod = manifest["spec"]
    assert pod["restartPolicy"] == "Never"
    assert pod["automountServiceAccountToken"] is False
    assert pod["hostNetwork"] is False
    assert pod["hostPID"] is False
    assert pod["hostIPC"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    container = pod["containers"][0]
    assert container["securityContext"]["runAsUser"] == 65534
    assert container["securityContext"]["runAsGroup"] == 65534
    assert container["securityContext"]["privileged"] is False
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"] == {"drop": ["ALL"]}


def test_pod_manifest_resource_limits():
    from agent_platform.sandbox.models import SandboxResources

    manifest = _manifest(resources=SandboxResources(cpu="500m", memory="512Mi"))
    resources = manifest["spec"]["containers"][0]["resources"]
    assert resources["requests"]["cpu"] == "500m"
    assert resources["requests"]["memory"] == "512Mi"
    assert resources["limits"]["cpu"] == "500m"
    assert resources["limits"]["memory"] == "512Mi"
    assert "ephemeral-storage" in resources["limits"]


def test_container_resources_requests_match_limits():
    from agent_platform.sandbox.models import SandboxResources

    resources = container_resources(SandboxResources(cpu="2", memory="4Gi"))
    assert resources["requests"] == resources["limits"]
    assert resources["limits"] == {
        "cpu": "2",
        "memory": "4Gi",
        "ephemeral-storage": "5Gi",
    }


def test_pod_manifest_never_inherits_host_env(monkeypatch):
    monkeypatch.setenv("KUBECONFIG", "/attacker/config")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak")
    manifest = _manifest()
    assert manifest["spec"]["containers"][0]["env"] == []


def test_pod_manifest_explicit_env_only():
    manifest = _manifest(env={"FOO": "bar"})
    assert manifest["spec"]["containers"][0]["env"] == [{"name": "FOO", "value": "bar"}]


def test_pod_manifest_metadata_overrides():
    manifest = _manifest(metadata={"container_user": "1000:1000", "read_only_rootfs": False})
    ctx = manifest["spec"]["containers"][0]["securityContext"]
    assert ctx["runAsUser"] == 1000
    assert ctx["runAsGroup"] == 1000
    assert ctx["readOnlyRootFilesystem"] is False
    assert manifest["spec"]["securityContext"]["runAsNonRoot"] is True


def test_pod_manifest_uid0_relaxes_run_as_non_root():
    manifest = _manifest(metadata={"container_user": "0:0"})
    assert manifest["spec"]["securityContext"]["runAsNonRoot"] is False
    assert manifest["spec"]["containers"][0]["securityContext"]["runAsUser"] == 0


def test_pod_manifest_labels_identify_sandbox():
    manifest = _manifest()
    labels = manifest["metadata"]["labels"]
    assert labels["agent-platform.io/sandbox-id"] == "sbx-1"
    assert labels["agent-platform.io/tenant-id"] == "tenant-1"
    assert labels["agent-platform.io/managed"] == "true"
    assert manifest["metadata"]["namespace"] == NAMESPACE


def test_pod_manifest_requires_workspace():
    with pytest.raises(ProviderError):
        build_pod_manifest(_spec(workspace_id=""))


@pytest.mark.parametrize(
    "mount",
    ["/", "/etc", "/etc/passwd", "/var/run", "/var/run/docker.sock", "/proc", "/sys"],
)
def test_pod_manifest_rejects_dangerous_mount_targets(mount):
    with pytest.raises(ProviderError):
        _manifest(mount_path=mount)


def test_pod_manifest_declared_ports_become_container_ports():
    manifest = _manifest(ports=(PortSpec(name="mcp", container_port=8080),))
    assert manifest["spec"]["containers"][0]["ports"] == [
        {"name": "mcp", "containerPort": 8080, "protocol": "TCP"}
    ]


def test_pod_manifest_without_ports_declares_no_ports():
    manifest = _manifest()
    assert "ports" not in manifest["spec"]["containers"][0]


def test_pod_manifest_working_dir_is_workspace_mount():
    manifest = _manifest()
    container = manifest["spec"]["containers"][0]
    assert container["workingDir"] == "/workspace"
    assert container["volumeMounts"] == [
        {"name": "workspace", "mountPath": "/workspace"}
    ]
    assert container["command"] == ["tail", "-f", "/dev/null"]


# --- workspace volume ---------------------------------------------------------


def test_workspace_volume_defaults_to_emptydir():
    volume = workspace_volume(_spec())
    assert volume == {"name": "workspace", "emptyDir": {}}


def test_workspace_volume_pvc_from_metadata():
    volume = workspace_volume(_spec(metadata={"workspace_pvc": "sbx-ws-1"}))
    assert volume == {
        "name": "workspace",
        "persistentVolumeClaim": {"claimName": "sbx-ws-1"},
    }


# --- labels / pod name --------------------------------------------------------


def test_pod_name_is_deterministic():
    assert pod_name("abc") == "agent-platform-sbx-abc"
    assert pod_name("abc") == pod_name("abc")


def test_sanitize_label_value():
    assert sanitize_label_value("tenant-1") == "tenant-1"
    cleaned = sanitize_label_value("Tenant Org!" + "x" * 100)
    assert len(cleaned) <= 63
    assert all(c.isalnum() or c in "-_." for c in cleaned)
    assert not cleaned.startswith(("-", ".", "_"))
    assert sanitize_label_value("!!!") == "unknown"


def test_parse_user():
    assert parse_user("65534:65534") == (65534, 65534)
    assert parse_user("1000") == (1000, 1000)
    with pytest.raises(ProviderError):
        parse_user("nobody")


# --- network policies ---------------------------------------------------------


def _netpols(mode, allowlist=(), cluster_cidrs=()):
    from agent_platform.sandbox.models import NetworkPolicy

    base = _spec(sandbox_id="sbx-1")
    spec = SandboxSpec(
        image=base.image,
        tenant_id=base.tenant_id,
        user_id=base.user_id,
        session_id=base.session_id,
        workspace=base.workspace,
        sandbox_id=base.sandbox_id,
        network_policy=NetworkPolicy(mode=mode, allowlist=allowlist),
    )
    return build_network_policies(spec, cluster_cidrs=cluster_cidrs)


def test_network_policy_none_denies_all():
    (policy,) = _netpols(NetworkMode.NONE)
    assert policy["spec"]["policyTypes"] == ["Ingress", "Egress"]
    assert "ingress" not in policy["spec"] and "egress" not in policy["spec"]
    # targets only this sandbox pod
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "agent-platform.io/sandbox-id": "sbx-1"
    }
    assert policy["metadata"]["name"] == "agent-platform-sbx-sbx-1-netpol"


def test_network_policy_internal_same_namespace_plus_dns():
    (policy,) = _netpols(NetworkMode.INTERNAL)
    assert policy["spec"]["ingress"] == [{"from": [{"podSelector": {}}]}]
    egress = policy["spec"]["egress"]
    assert {"to": [{"podSelector": {}}]} in egress
    dns = [rule for rule in egress if rule.get("ports")]
    assert dns and all(p["port"] == 53 for p in dns[0]["ports"])


def test_network_policy_allowlist_becomes_ipblock_egress():
    (policy,) = _netpols(NetworkMode.ALLOWLIST, allowlist=("8.8.8.8", "10.0.0.0/8"))
    egress = policy["spec"]["egress"]
    cids = {
        entry["ipBlock"]["cidr"]
        for rule in egress
        for entry in rule.get("to", [])
        if "ipBlock" in entry
    }
    assert cids == {"8.8.8.8/32", "10.0.0.0/8"}


def test_network_policy_internet_only_denies_cluster_cidrs():
    (policy,) = _netpols(
        NetworkMode.INTERNET_ONLY, cluster_cidrs=("10.0.0.0/8", "172.16.0.0/12")
    )
    ip_block = policy["spec"]["egress"][0]["to"][0]["ipBlock"]
    assert ip_block["cidr"] == "0.0.0.0/0"
    assert ip_block["except"] == ["10.0.0.0/8", "172.16.0.0/12"]


def test_network_policy_internet_only_without_cidrs_is_documented_limitation():
    assert _netpols(NetworkMode.INTERNET_ONLY) == []


def test_network_policy_full_is_unrestricted():
    assert _netpols(NetworkMode.FULL) == []


# --- endpoints / exec argv / download -----------------------------------------


def test_resolve_endpoints_uses_pod_ip():
    endpoints = resolve_endpoints(
        (PortSpec(name="mcp", container_port=8080),), pod_ip="10.1.2.3"
    )
    assert endpoints[0].name == "mcp"
    assert endpoints[0].address == "10.1.2.3:8080"


def test_resolve_endpoints_without_pod_ip_fails_creation():
    with pytest.raises(ProviderError):
        resolve_endpoints((PortSpec(name="mcp", container_port=8080),), pod_ip=None)
    assert resolve_endpoints((), pod_ip=None) == []


def test_build_exec_argv_string_command_with_wrapper():
    argv = build_exec_argv("echo hi", timeout=2.4, wrapper_available=True)
    assert argv == ["timeout", "-s", "KILL", "3", "/bin/sh", "-c", "cd /workspace && echo hi"]


def test_build_exec_argv_list_command_uses_exec_dollar_at():
    argv = build_exec_argv(["echo", "hi"], timeout=5, wrapper_available=False)
    assert argv == ["/bin/sh", "-c", 'cd /workspace && exec "$@"', "sh", "echo", "hi"]


def test_build_exec_argv_injects_env_and_cwd_quoting():
    argv = build_exec_argv(
        ["python", "-c", "1"], cwd="/workspace/my dir", env={"A": "b"}, wrapper_available=True
    )
    assert argv[:6] == ["timeout", "-s", "KILL", "60", "env", "A=b"]
    assert argv[6:8] == ["/bin/sh", "-c"]
    assert argv[8] == "cd '/workspace/my dir' && exec \"$@\""


def test_build_exec_argv_rejects_empty_command():
    with pytest.raises(ProviderError):
        build_exec_argv([])


def test_download_argv_tars_relative_path_and_rejects_outside():
    argv = download_argv("/workspace/output/a.txt", "/workspace")
    assert argv == ["/bin/sh", "-c", 'cd /workspace && tar -cf - output/a.txt | base64']
    with pytest.raises(PermissionDenied):
        download_argv("/etc/passwd", "/workspace")


def test_decode_base64_output_tolerates_wrapped_lines():
    payload = decode_base64_output("aGVs\nbG8=")
    assert payload == b"hello"


# --- startup-state helpers (kubernetes SDK models, no cluster) ----------------


def _v1pod(phase, waiting_reason=None, running=False):
    from kubernetes.client import (
        V1ContainerState,
        V1ContainerStateRunning,
        V1ContainerStateWaiting,
        V1ContainerStatus,
        V1Pod,
        V1PodStatus,
    )

    state = V1ContainerState(
        running=V1ContainerStateRunning() if running else None,
        waiting=V1ContainerStateWaiting(reason=waiting_reason) if waiting_reason else None,
    )
    return V1Pod(
        status=V1PodStatus(
            phase=phase,
            container_statuses=[
                V1ContainerStatus(
                    name="sandbox",
                    ready=running,
                    restart_count=0,
                    image="x",
                    image_id="x",
                    state=state,
                    last_state=None,
                )
            ],
        )
    )


def test_pod_ready_requires_running_containers():
    assert pod_ready(_v1pod("Running", running=True))
    assert not pod_ready(_v1pod("Running", waiting_reason="ContainerCreating"))
    assert not pod_ready(_v1pod("Pending"))
    assert not pod_ready(_v1pod("Running", waiting_reason="CrashLoopBackOff"))


def test_pod_startup_failure_distinguishes_terminal_from_transient():
    assert pod_startup_failure(_v1pod("Pending", waiting_reason="ContainerCreating")) is None
    assert pod_startup_failure(_v1pod("Running", waiting_reason="CrashLoopBackOff")) == (
        "container sandbox: CrashLoopBackOff"
    )
    assert "Failed" in pod_startup_failure(_v1pod("Failed"))
    assert pod_startup_failure(_v1pod("Running", running=True)) is None


# --- cluster-backed contract tests (skipped without a cluster) ----------------

_K8S_STATE: bool | None = None


def _k8s_available() -> bool:
    global _K8S_STATE
    if _K8S_STATE is None:
        try:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            client.CoreV1Api().list_namespaced_pod("default", limit=1)
            _K8S_STATE = True
        except Exception:  # noqa: BLE001 - any failure means "skip"
            _K8S_STATE = False
    return _K8S_STATE


def _require_k8s():
    if not _k8s_available():
        pytest.skip("Kubernetes cluster not available")


def _k8s_manager(namespace: str = "default") -> SandboxManager:
    registry = SandboxProviderRegistry()
    registry.register("kubernetes", KubernetesSandboxProvider(namespace=namespace))
    return SandboxManager(registry, default_provider="kubernetes")


def test_k8s_provider_passes_full_contract_suite():
    _require_k8s()
    run_provider_contract_suite(
        lambda: _k8s_manager(),
        spec=_spec(image=ALPINE),
    )


def test_k8s_execute_enforces_timeout():
    _require_k8s()

    async def scenario():
        manager = _k8s_manager()
        sandbox = await manager.create(_spec(image=ALPINE))
        result = await manager.execute(
            sandbox.sandbox_id,
            ExecutionRequest(request_id="slow", command="sleep 5", timeout=1.0),
        )
        assert result.timed_out is True
        assert result.exit_code in (124, 137)
        await manager.destroy(sandbox.sandbox_id)

    asyncio.run(scenario())

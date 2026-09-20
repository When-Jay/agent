import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src" / "agent_platform"


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_runtime_core_does_not_depend_on_execution_runtimes_or_infrastructure():
    # deepagents-runtime-spec.md: Runtime Core must not import LangChain/DeepAgents.
    forbidden_prefixes = (
        "agent_platform.runtime.agent",
        "agent_platform.runtime.workflow",
        "agent_platform.runtime.dispatch",
        "agent_platform.runtime.capabilities",
        "agent_platform.sandbox",
        "agent_platform.infrastructure",
        "agent_platform.mcp",
        "agent_platform.observability",
        "agent_platform.evaluation",
        "langchain",
        "deepagents",
        "celery",
        "langgraph",
    )

    offenders = []
    for path in (SRC_ROOT / "runtime" / "core").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_agent_runtime_does_not_depend_on_api_or_infrastructure():
    forbidden_prefixes = (
        "agent_platform.api",
        "agent_platform.infrastructure",
        "agent_platform.mcp",
        "agent_platform.observability",
        "agent_platform.evaluation",
        "agent_platform.runtime.dispatch",
    )

    offenders = []
    for path in (SRC_ROOT / "runtime" / "agent").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_workflow_runtime_does_not_depend_on_api_or_infrastructure():
    forbidden_prefixes = (
        "agent_platform.api",
        "agent_platform.infrastructure",
        "agent_platform.mcp",
        "agent_platform.observability",
        "agent_platform.evaluation",
        "agent_platform.runtime.dispatch",
    )

    offenders = []
    for path in (SRC_ROOT / "runtime" / "workflow").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_sandbox_core_does_not_depend_on_api_or_infrastructure():
    forbidden_prefixes = (
        "agent_platform.api",
        "agent_platform.infrastructure",
        "agent_platform.mcp",
        "agent_platform.observability",
        "agent_platform.evaluation",
    )

    offenders = []
    for path in (SRC_ROOT / "sandbox").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_api_does_not_import_agent_or_workflow_execution_modules():
    forbidden_prefixes = (
        "agent_platform.runtime.agent",
        "agent_platform.runtime.workflow",
    )

    offenders = []
    for path in (SRC_ROOT / "api").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_mcp_module_stays_sdk_free():
    # mcp-gateway-spec.md section 3: the official MCP SDK is used only by
    # the composition root and tests; agent_platform/mcp/ speaks to
    # sessions through its own duck-typed ports and never imports the
    # SDK (the `mcp` or `mcp_types` distributions).
    offenders = []
    for path in (SRC_ROOT / "mcp").rglob("*.py"):
        for imported in _imports_for(path):
            if imported == "mcp" or imported.startswith("mcp."):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_evolution_module_does_not_depend_on_execution_or_infrastructure():
    # 060-evolution.md: Evolution consumes Evaluation through a stable
    # gateway and never executes Agent/Workflow/tools or touches
    # infrastructure directly (composition roots wire stores/dispatch).
    forbidden_prefixes = (
        "agent_platform.api",
        "agent_platform.infrastructure",
        "agent_platform.mcp",
        "agent_platform.observability",
        "agent_platform.sandbox",
        "agent_platform.runtime.agent",
        "agent_platform.runtime.workflow",
        "agent_platform.runtime.dispatch",
        "celery",
        "langchain",
        "langgraph",
    )

    offenders = []
    for path in (SRC_ROOT / "evolution").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_evolution_domain_stays_self_contained():
    # Evolution domain must not import other platform modules (mirrors the
    # Runtime Core rule): only stdlib, keeping the aggregate shapes pure.
    forbidden_prefixes = (
        "agent_platform.evaluation",
        "agent_platform.evolution.application",
        "agent_platform.evolution.experiment",
        "agent_platform.runtime",
    )

    offenders = []
    for path in (SRC_ROOT / "evolution" / "domain").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_knowledge_module_boundary():
    # knowledge-rag-spec.md section 1: the knowledge module stays free of
    # platform layers and execution runtimes; runtime.capabilities (the
    # interface home) is its allowed runtime dependency.
    forbidden_prefixes = (
        "agent_platform.api",
        "agent_platform.infrastructure",
        "agent_platform.mcp",
        "agent_platform.observability",
        "agent_platform.evaluation",
        "agent_platform.evolution",
        "agent_platform.sandbox",
        "agent_platform.runtime.agent",
        "agent_platform.runtime.workflow",
        "agent_platform.runtime.dispatch",
        "celery",
        "langgraph",
        "deepagents",
    )

    offenders = []
    for path in (SRC_ROOT / "knowledge").rglob("*.py"):
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []


def test_knowledge_langchain_confined_to_seams():
    # knowledge-rag-spec.md section 1: LangChain is confined to the two
    # declared seams (chunking.py splitter, embeddings.py adapter).
    allowed_seams = {"chunking.py", "embeddings.py"}
    forbidden_prefixes = ("langchain",)

    offenders = []
    for path in (SRC_ROOT / "knowledge").rglob("*.py"):
        if path.name in allowed_seams:
            continue
        for imported in _imports_for(path):
            if imported.startswith(forbidden_prefixes):
                offenders.append((path.relative_to(PROJECT_ROOT).as_posix(), imported))

    assert offenders == []

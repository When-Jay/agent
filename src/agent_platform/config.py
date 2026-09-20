from dataclasses import dataclass, field
from os import getenv


def _env_float(name: str, default: float) -> float:
    raw = getenv(name, "").strip()
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = getenv(name, "").strip()
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    # Empty values fall back to the default: docker-compose passes unset
    # variables through as empty strings (${VAR:-}).
    raw = getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_name: str = field(default_factory=lambda: getenv("APP_NAME", "ai-runtime-platform"))
    environment: str = field(default_factory=lambda: getenv("ENVIRONMENT", "local"))
    version: str = field(default_factory=lambda: getenv("APP_VERSION", "0.1.0"))
    log_level: str = field(default_factory=lambda: getenv("LOG_LEVEL", "INFO"))
    database_url: str = field(default_factory=lambda: getenv("DATABASE_URL", "sqlite:///:memory:"))
    redis_url: str = field(default_factory=lambda: getenv("REDIS_URL", "redis://localhost:6379/0"))
    celery_task_always_eager: bool = field(
        default_factory=lambda: getenv("CELERY_TASK_ALWAYS_EAGER", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    redis_event_fanout_enabled: bool = field(
        default_factory=lambda: getenv("REDIS_EVENT_FANOUT_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    langfuse_public_key: str = field(default_factory=lambda: getenv("LANGFUSE_PUBLIC_KEY", ""))
    langfuse_secret_key: str = field(default_factory=lambda: getenv("LANGFUSE_SECRET_KEY", ""))
    langfuse_host: str = field(
        default_factory=lambda: getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    )
    # MCP gateway server declarations (mcp-gateway-spec.md section 4):
    # JSON array of {name, url, transport?, credential_ref?, side_effects?}.
    mcp_servers_json: str = field(default_factory=lambda: getenv("MCP_SERVERS_JSON", ""))
    # API hardening (plan-productionization task 3). cors_allow_origins is a
    # comma-separated origin list; empty = same-origin only (CORS disabled).
    cors_allow_origins: str = field(default_factory=lambda: getenv("CORS_ALLOW_ORIGINS", ""))
    max_request_body_mb: float = field(default_factory=lambda: _env_float("MAX_REQUEST_BODY_MB", 10))
    rate_limit_rpm: int = field(default_factory=lambda: _env_int("RATE_LIMIT_RPM", 120))
    rate_limit_enabled: bool = field(default_factory=lambda: _env_bool("RATE_LIMIT_ENABLED", True))
    # Evaluation 巡检（plan-productionization G4）：cron 为空 = 不注册 beat
    # 调度；application_id 是巡检评测 Run 的目标应用，未设置时任务跳过。
    evaluation_patrol_cron: str = field(default_factory=lambda: getenv("EVALUATION_PATROL_CRON", ""))
    evaluation_patrol_asset: str = field(
        default_factory=lambda: getenv("EVALUATION_PATROL_ASSET", "regression-set")
    )
    evaluation_patrol_application_id: str = field(
        default_factory=lambda: getenv("EVALUATION_PATROL_APPLICATION_ID", "")
    )
    # DB 连接池（仅 Postgres 方言生效；SQLite 路径不变）。多进程部署时
    # Postgres max_connections 必须覆盖 workers × (pool_size + max_overflow)
    # 的总和并留余量。recycle 单位秒，避免被服务端/中间件静默断开。
    db_pool_size: int = field(default_factory=lambda: _env_int("DB_POOL_SIZE", 10))
    db_max_overflow: int = field(default_factory=lambda: _env_int("DB_MAX_OVERFLOW", 20))
    db_pool_recycle: int = field(default_factory=lambda: _env_int("DB_POOL_RECYCLE", 1800))

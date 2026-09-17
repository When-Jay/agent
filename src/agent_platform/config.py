from dataclasses import dataclass, field
from os import getenv


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

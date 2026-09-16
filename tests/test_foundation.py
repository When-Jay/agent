import logging

from agent_platform.config import Settings
from agent_platform.logging import configure_logging


def test_settings_load_default_local_configuration():
    settings = Settings()

    assert settings.app_name == "ai-runtime-platform"
    assert settings.environment == "local"
    assert settings.database_url == "sqlite:///:memory:"
    assert settings.redis_url == "redis://localhost:6379/0"


def test_settings_read_environment_at_instantiation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@db/platform")

    settings = Settings()

    assert settings.database_url == "postgresql+psycopg://user:pass@db/platform"


def test_logging_configuration_sets_root_level():
    configure_logging("DEBUG")

    assert logging.getLogger().level == logging.DEBUG

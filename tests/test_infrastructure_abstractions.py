from agent_platform.infrastructure.database import DatabaseConnection
from agent_platform.infrastructure.redis import RedisConnection


def test_database_connection_exposes_url_without_connecting():
    connection = DatabaseConnection("postgresql+psycopg://user:pass@db/platform")

    assert connection.url == "postgresql+psycopg://user:pass@db/platform"
    assert connection.driver == "postgresql+psycopg"


def test_database_connection_connects_with_local_in_memory_default():
    connection = DatabaseConnection("sqlite:///:memory:")

    assert connection.driver == "sqlite"
    assert connection.check() is True
    assert connection.engine is connection.engine


def test_redis_connection_exposes_url_without_importing_client_library():
    connection = RedisConnection("redis://localhost:6379/0")

    assert connection.url == "redis://localhost:6379/0"
    assert connection.scheme == "redis"

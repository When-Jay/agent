from dataclasses import dataclass
from functools import cached_property

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class DatabaseConnection:
    url: str

    @property
    def driver(self) -> str:
        return self.url.split("://", maxsplit=1)[0]

    @cached_property
    def engine(self) -> Engine:
        return create_engine(self.url)

    def check(self) -> bool:
        """Verify connectivity with a trivial query, without assuming any schema."""
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True

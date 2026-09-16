from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class RedisConnection:
    url: str

    @property
    def scheme(self) -> str:
        return urlparse(self.url).scheme

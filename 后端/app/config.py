from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_version: str
    neo4j_http_url: str
    neo4j_database: str
    neo4j_user: str
    neo4j_password: str
    neo4j_timeout_seconds: float
    cors_origins: list[str]

    @property
    def neo4j_query_api_url(self) -> str:
        base_url = self.neo4j_http_url.rstrip("/")
        database = self.neo4j_database.strip("/")
        return f"{base_url}/db/{database}/query/v2"

    def require_neo4j_password(self) -> None:
        if not self.neo4j_password:
            raise ConfigError("NEO4J_PASSWORD is not configured. Set it in backend/.env or as an environment variable.")


@lru_cache
def get_settings() -> Settings:
    _load_dotenv(BASE_DIR / ".env")

    return Settings(
        app_name=os.getenv("APP_NAME", "Job Skill Graph API"),
        app_version=os.getenv("APP_VERSION", "0.1.0"),
        neo4j_http_url=os.getenv("NEO4J_HTTP_URL", "http://127.0.0.1:7474"),
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
        neo4j_password=os.getenv("NEO4J_PASSWORD", "123456yXr!"),
        neo4j_timeout_seconds=float(os.getenv("NEO4J_TIMEOUT_SECONDS", "15")),
        cors_origins=_split_csv(os.getenv("CORS_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173")),
    )

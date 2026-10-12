from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://USER:PASSWORD@localhost:5432/contamination_cc"

    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_use_tls: bool = False  # set true for a cloud broker (e.g. HiveMQ Cloud on 8883)

    retention_hours: int = 12
    baselining_period_seconds: int = 90

    node_orchestrator_url: str = "http://localhost:8100"

    log_level: str = "INFO"

    registry_path: str = str(REPO_ROOT / "config" / "registry.json")

    # Where nodes download pushed model files from (§6: HTTP download, the closer analog to a
    # real OTA flow) -- this service's own externally-reachable base URL.
    public_url: str = "http://localhost:8000"
    model_files_dir: str = str(REPO_ROOT / "model_files")

    # Comma-separated list of origins dashboard/ is served from, e.g.
    # "https://your-app.vercel.app,http://localhost:5173". "*" (the local-demo default) means
    # any origin -- fine for a hackathon on localhost, not for a public deployment.
    cors_allow_origins: str = "*"

    # Sized down from what local concurrent-registration testing used (30/20), since managed
    # Postgres free tiers (e.g. Supabase) cap total connections much lower than a local instance
    # would -- but 5/5 proved too small under a real running 15-node simulation's sustained MQTT
    # alert/status/summary traffic (production saw QueuePool timeouts). This is a middle ground;
    # if a deploy still sees pool timeouts under a larger node_count, raise these via the
    # DB_POOL_SIZE/DB_MAX_OVERFLOW env vars rather than editing the default, and confirm it stays
    # under the DB provider's actual connection cap first.
    db_pool_size: int = 10
    db_max_overflow: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()

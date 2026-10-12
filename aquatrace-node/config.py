from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mqtt_broker_host: str = "localhost"
    mqtt_broker_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_use_tls: bool = False  # set true for a cloud broker (e.g. HiveMQ Cloud on 8883)

    sim_time_scale: float = 120.0
    default_node_count: int = 15
    sim_seed: int = 42

    model_artifact_dir: str = str(REPO_ROOT / "ml" / "artifacts")

    cc_api_base_url: str = "http://localhost:8000"
    orchestrator_port: int = 8100

    log_level: str = "INFO"

    registry_path: str = str(REPO_ROOT / "config" / "registry.json")


@lru_cache
def get_settings() -> Settings:
    return Settings()

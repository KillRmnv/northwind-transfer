from pydantic import PositiveFloat
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env",), extra="ignore")

    db_profile: str = "postgres"

    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "transfer"
    db_user: str = "transfer"
    db_password: str = "transfer"

    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_pre_ping: bool = True

    sqlite_path: str = "./var/transfer.sqlite3"
    data_dir: str = "./data"

    mock_provider_url: str = "http://mock-provider:9000"
    mock_provider_timeout_sec: float = 5.0
    provider_probe_delay_sec: PositiveFloat = PositiveFloat(2.0)
    provider_visibility_grace_sec: int = 30

    provider_webhook_secret: str = ""

    public_base_url: str = "http://api:8080"

    api_sync_timeout_sec: float = 12.0
    reconcile_interval_sec: float = 60.0
    log_level: str = "INFO"
    env: str = "dev"

    @property
    def database_url(self) -> str:
        if self.db_profile == "postgres":
            return (
                f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        if self.db_profile == "mysql":
            return (
                f"mysql+aiomysql://{self.db_user}:{self.db_password}"
                f"@{self.db_host}:{self.db_port}/{self.db_name}"
            )
        if self.db_profile == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        raise ValueError(f"unknown DB_PROFILE: {self.db_profile}")

    @property
    def is_dev(self) -> bool:
        return self.env == "dev"


settings = Settings()

"""Application configuration management."""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # API Configuration
    api_key: str
    host: str = "0.0.0.0"
    port: int = 8000
    base_url: str = "http://localhost:8000"

    # Concurrency Control
    max_concurrent_tasks: int = 5

    # Generation Configuration
    default_timeout: int = 80
    proxy: str | None = "http://127.0.0.1:7897"
    use_proxy: bool = True

    # Storage Configuration
    storage_dir: Path = Path("./static/generated")
    cleanup_hours: int = 24

    # Cookie Configuration
    cookies_path: Path = Path("./data/cookies.json")
    accounts_dir: Path = Path("./data/accounts")
    per_account_concurrent_tasks: int = 1
    account_cooldown_seconds: int = 600

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    @property
    def effective_proxy(self) -> str | None:
        """Return proxy URL if enabled, None otherwise."""
        return self.proxy if self.use_proxy else None


# Global settings instance
settings = Settings()

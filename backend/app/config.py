from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
ROOT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "OpsPilot"
    app_version: str = "0.1.0"
    environment: str = "development"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    database_url: str = f"sqlite:///{BACKEND_DIR / 'opspilot.db'}"
    # Comma-separated list of allowed browser origins.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Synthetic recruitment app the agent operates (demo-app/app, see demo-app/app/main.py).
    demo_app_url: str = "http://127.0.0.1:5050"  # macOS AirPlay Receiver holds 5000
    # Set AGENT_HEADLESS=false to watch the browser (e.g. for the demo video).
    agent_headless: bool = True
    demo_app_timeout_seconds: float = 5.0
    # Failure recovery: total attempts for a mutating action, and wait between them.
    agent_max_attempts: int = 3
    agent_retry_backoff_seconds: float = 1.0
    # Pause between agent steps so a live run is watchable in the dashboard / demo video.
    agent_step_delay_seconds: float = 0.3

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()

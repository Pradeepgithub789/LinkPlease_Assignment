from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TESTING: bool = False
    PSEUDOGRAM_API_KEY: str = "dummy_key"
    PSEUDOGRAM_BASE_URL: str = "https://pseudogram-api.onrender.com"
    DATABASE_URL: str = "sqlite:///./linkplease.db"
    WEBHOOK_SIGNATURE_REQUIRED: bool = True
    MAX_DM_ATTEMPTS: int = 5
    MAX_RECONCILIATION_POLLS: int = 10
    RECONCILIATION_INTERVAL_SECONDS: float = 2.0
    WORKER_POLL_INTERVAL_SECONDS: float = 1.0
    RATE_LIMIT_WINDOW_SECONDS: float = 60.0
    STUCK_JOB_TIMEOUT_SECONDS: float = 300.0

    class Config:
        env_file = ".env"

settings = Settings()


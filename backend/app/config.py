from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_ENV: str = "development"
    DATABASE_URL: str = "sqlite:///./tradex.db"
    BOT_TOKEN: str = ""
    BOT_USERNAME: str = "TradeXBot"
    WEBAPP_URL: str = "http://localhost:8000/"
    JWT_SECRET: str = DEFAULT_JWT_SECRET
    JWT_EXPIRE_MINUTES: int = 60 * 24 * 7
    ADMIN_TELEGRAM_IDS: str = ""
    DEV_AUTH_BYPASS: bool = False
    START_BALANCE: float = 10000.0
    REFERRAL_BONUS: float = 500.0
    REFERRAL_XP: int = 100
    DAILY_REWARD: float = 100.0
    DAILY_REWARD_XP: int = 20
    PRICE_CACHE_SECONDS: int = 30
    RATE_LIMIT_PER_MINUTE: int = 120

    @property
    def admin_ids(self) -> set[int]:
        return {int(x) for x in self.ADMIN_TELEGRAM_IDS.split(",") if x.strip().isdigit()}

    @property
    def dev_bypass_enabled(self) -> bool:
        return self.DEV_AUTH_BYPASS and self.APP_ENV != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

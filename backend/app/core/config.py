from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SVC Infosystems Service Desk"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://svc:svc@db:5432/svc"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str = "change-this-development-secret"
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    cors_origins: str = "http://localhost:3000"
    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    mail_from: str = "no-reply@svcinfosys.in"
    debug: bool = False
    auth_rate_limit: int = 5
    auth_rate_window_seconds: int = 60
    trusted_proxy_enabled: bool = False
    trusted_proxy_ips: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if not self.debug and (len(self.jwt_secret) < 32 or self.jwt_secret.startswith(("change-this", "replace-"))):
            raise ValueError("JWT_SECRET must be at least 32 characters and non-default outside DEBUG")
        if "*" in self.cors_origin_list:
            raise ValueError("CORS_ORIGINS must be an explicit origin allow-list")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_proxy_ip_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_ips.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

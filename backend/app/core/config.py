from functools import lru_cache

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SVC Infosystems Service Desk"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://svc:svc@db:5432/svc"
    redis_url: str = "redis://redis:6379/0"
    jwt_secret: str
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    cors_origins: str = "http://localhost:3000"
    smtp_host: str = "mailhog"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    smtp_use_ssl: bool = False
    mail_from: str = "no-reply@svcinfosys.in"
    app_base_url: str = "http://localhost:3000"
    invitation_ttl_hours: int = 24
    invite_allowed_email_domains: str = ""
    debug: bool = False
    auth_rate_limit: int = 5
    auth_rate_window_seconds: int = 60
    invite_rate_limit: int = 30
    invite_rate_window_seconds: int = 3600
    invite_accept_rate_limit: int = 10
    invite_accept_rate_window_seconds: int = 900
    trusted_proxy_enabled: bool = False
    trusted_proxy_ips: str = ""
    upload_root: str = "data/documents"
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_bucket: str | None = None
    s3_region: str = "auto"

    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_postgres_url(cls, value: str) -> str:
        if value.startswith("sqlite"):
            return value
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql+asyncpg://", 1)
        elif value.startswith("postgresql://"):
            value = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        if "sslmode=" in value:
            value = value.replace("sslmode=require", "ssl=require").replace("sslmode=prefer", "ssl=prefer").replace("sslmode=verify-ca", "ssl=require").replace("sslmode=verify-full", "ssl=require")
        if "channel_binding=" in value:
            import re
            value = re.sub(r"[?&]channel_binding=[^&]*", "", value)
            if "?" not in value and "&" in value:
                value = value.replace("&", "?", 1)
        return value

    @model_validator(mode="before")
    @classmethod
    def require_jwt_secret(cls, values: object) -> object:
        if not isinstance(values, dict) or not values.get("jwt_secret"):
            raise ValueError("JWT_SECRET is required from the environment in every mode")
        return values

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        smtp_password_configured = bool(
            self.smtp_password and self.smtp_password.get_secret_value()
        )
        if not self.debug and (len(self.jwt_secret) < 32 or self.jwt_secret.startswith(("change-this", "replace-"))):
            raise ValueError("JWT_SECRET must be at least 32 characters and non-default outside DEBUG")
        if "*" in self.cors_origin_list:
            raise ValueError("CORS_ORIGINS must be an explicit origin allow-list")
        if self.smtp_starttls and self.smtp_use_ssl:
            raise ValueError("SMTP_STARTTLS and SMTP_USE_SSL cannot both be enabled")
        if bool(self.smtp_username) != smtp_password_configured:
            raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be configured together")
        if self.smtp_username and not (self.smtp_starttls or self.smtp_use_ssl):
            raise ValueError("Authenticated SMTP requires STARTTLS or SSL")
        if self.invitation_ttl_hours < 1:
            raise ValueError("INVITATION_TTL_HOURS must be at least 1")
        if min(
            self.auth_rate_limit,
            self.auth_rate_window_seconds,
            self.invite_rate_limit,
            self.invite_rate_window_seconds,
            self.invite_accept_rate_limit,
            self.invite_accept_rate_window_seconds,
        ) < 1:
            raise ValueError("Rate limits and rate windows must be positive")
        self.app_base_url = self.app_base_url.rstrip("/")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_proxy_ip_list(self) -> list[str]:
        return [item.strip() for item in self.trusted_proxy_ips.split(",") if item.strip()]

    @property
    def invite_allowed_email_domain_list(self) -> list[str]:
        return [
            domain.strip().casefold()
            for domain in self.invite_allowed_email_domains.split(",")
            if domain.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()

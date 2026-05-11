from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, AnyHttpUrl, field_validator,SecretStr
from typing import List,Optional
from functools import lru_cache
import re

from app.utils.path import env_file
from app.core.exceptions import *
from app.utils.log import log


class _WebConfig(BaseSettings):
    """
    Configuration for web application
    Parameters:
    - SERVER_HOST: Host for the web server
    - SERVER_PORT: Port for the web server
    - ALLOWED_ORIGINS: List of allowed origins for CORS
    """

    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )
    app_name: str = Field(default="ScorbVPN Dashboard", validation_alias="APP_NAME")
    app_version: str = Field(default="0.1.0", validation_alias="APP_VERSION")
    server_host: str = Field(default_factory=lambda: '127.0.0.1', validation_alias="SERVER_HOST")
    server_port: int = Field(default=8000, validation_alias="SERVER_PORT", ge=1, le=65535)
    allowed_origins: List[AnyHttpUrl] = Field(default_factory=list, validation_alias="ALLOWED_ORIGINS")
    web_superadmin_username: str = Field(default="admin", validation_alias="WEB_SUPERADMIN_USERNAME")
    web_superadmin_password: SecretStr = Field(default_factory=lambda: SecretStr("SUPERADMIN"), validation_alias="WEB_SUPERADMIN_PASSWORD")
    metrics_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), validation_alias="METRICS_API_KEY")
    site_url: str = Field(default="", description="Public site URL, e.g. https://example.com:8443", validation_alias="SITE_URL")
    
    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, values: List[AnyHttpUrl]) -> List[AnyHttpUrl]:
        for value in values:
            if value.host in ["localhost", "127.0.0.1"]:
                log.warning(f"⚠️ Using localhost in allowed origins: {value}")
        return values
    
    @field_validator("app_name")
    @classmethod
    def validate_app_name(cls, value: str) -> str:
        if not value.strip():
            raise WebException("App name cannot be empty")
        return value.strip()
    
    @field_validator("app_version")
    @classmethod
    def validate_app_version(cls, value: str) -> str:
        if not value.strip():
            raise WebException("App version cannot be empty")
        return value.strip()

    
    @field_validator("server_host")
    @classmethod
    def validate_server_host(cls, value):
        if not re.match(r'^[\w\.-]+$', value):
            raise WebException(f"Invalid server host: {value}. Must be a valid hostname or IP address")
        return value


    @field_validator("web_superadmin_password")
    @classmethod
    def validate_superadmin_password(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 6:
            raise WebException("Superadmin password must be at least 6 characters long")
        return value

    @field_validator("web_superadmin_username")
    @classmethod
    def validate_superadmin_username(cls, value: str) -> str:
        if len(value) < 3:
            raise WebException("Superadmin username must be at least 3 characters long")
        return value.strip()
    
@lru_cache()
def get_web_config() -> _WebConfig:
    return _WebConfig()

try:
    web_config = get_web_config()
    log.info("✅ Web configuration loaded successfully")
except WebException as e:
    log.error(f"❌ Web configuration error: {e}")
    raise
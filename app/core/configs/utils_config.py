from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, model_validator, field_validator
from functools import lru_cache
from typing import Literal
from pathlib import Path
import re

from app.utils.path import env_file
from app.core.exceptions import EnvException
from app.utils.log import log

class _UtilsConfig(BaseSettings):
    """
    Configuration for utility settings such as logging
    Parameters:
    - LOG_PATH: Path to store log files (default: "app/logs")
    - LOG_ROTATION: Log rotation policy (default: "1 day")
    - LOG_RETENTION: Log retention policy (default: "30 days")
    - LOG_LEVEL: Log level (default: "INFO")
    """
    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )
    
    log_path: Path = Field(default=Path("app/logs"), validation_alias="LOG_PATH")
    log_rotation: str = Field(default="1 day", validation_alias="LOG_ROTATION")
    log_retention: str = Field(default="30 days", validation_alias="LOG_RETENTION")
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL"
    )
    redis_url: str = Field(default="", validation_alias="REDIS_URL")
    
    @field_validator('log_path', mode='before')
    @classmethod
    def validate_log_path(cls, value):
        return Path(value) if value else Path("app/logs")

    @field_validator('log_rotation')
    def validate_rotation(cls, value):
        if value is None:
            return "1 day"
        valid = re.fullmatch(r'^\d+\s*(day|days|hour|hours|MB|GB)$', str(value))
        if not valid:
            raise EnvException(f"Invalid format for rotation: {value}. Example: 1 day, 100 MB")
        return str(value)
    
    @field_validator('log_retention')
    def validate_retention(cls, value):
        if value is None:
            return "30 days"
        valid = re.fullmatch(r'^\d+\s*(day|days|month|months)$', str(value))
        if not valid:
            raise EnvException(f"Invalid format for retention: {value}. Example: 30 days")
        return str(value)
    
    @field_validator('log_level')
    def validate_level(cls, value):
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if value.upper() not in valid_levels:
            raise EnvException(f"Level logs can be one of: {valid_levels}")
        return value.upper()

    @field_validator("redis_url")
    def validate_redis_url(cls, value: str) -> str:
        if not value:
            return ""
        value = value.strip()
        if not re.fullmatch(r"^rediss?://.+", value):
            raise EnvException(
                "REDIS_URL must start with redis:// or rediss://"
            )
        return value
    
    @model_validator(mode='after')
    def check_log_settings(self):
        if int(self.log_rotation.split()[0]) == 0:
            raise EnvException("Rotation cannot be zero")
        return self

@lru_cache()
def get_utils_config() -> _UtilsConfig:
    return _UtilsConfig()

try:
    utils = get_utils_config()
    log.success("✅ Utils config initialized successfully")
    log.debug(f"Utils: {utils}")
except Exception as e:
    log.error(f"❌ Failed to initialize Utils config: {e}")

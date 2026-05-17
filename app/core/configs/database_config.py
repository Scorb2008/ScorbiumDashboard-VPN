from pydantic import Field, SecretStr, model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal, Optional
from functools import lru_cache
import ipaddress
import re

from app.utils.log import log
from app.utils.path import env_file
from app.core.exceptions import *

class _DatabaseConfig(BaseSettings):
    """
    Configuration for database connection
    Parameters:
    - DB_ENGINE: Database engine type (e.g. postgresql)
    - DB_NAME: Database name
    - DB_HOST: Database host (e.g. localhost)
    - DB_PORT: Database port (e.g. 5432)
    - DB_USER: Database username
    - DB_PASSWORD: Database password
    """
    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )

    db_engine: Literal["postgresql"] = Field(
        default=..., validation_alias="DB_ENGINE"
    )
    db_name: str = Field(default="vpnbot", validation_alias="DB_NAME")

    db_host: str = Field(default="localhost", validation_alias="DB_HOST")
    db_port: int = Field(default=5432, validation_alias="DB_PORT", ge=1, le=65535)
    db_user: str = Field(default="postgres", validation_alias="DB_USER")
    db_password: SecretStr = Field(
        default=..., validation_alias="DB_PASSWORD"
    )

    @model_validator(mode="after")
    def validate_db_config(self) -> "_DatabaseConfig":
        """Validate database configuration based on engine type"""
        if self.db_engine == "postgresql":
            missing_fields = []
            
            if not self.db_host:
                missing_fields.append("DB_HOST")
            if not self.db_port:
                missing_fields.append("DB_PORT")
            if not self.db_user:
                missing_fields.append("DB_USER")
            if not self.db_password:
                missing_fields.append("DB_PASSWORD")
                
            if missing_fields:
                raise DatabaseValueError(
                    f"For PostgreSQL must be specified: {', '.join(missing_fields)}"
                )
                
            log.info(f"✅ PostgreSQL configuration validated for database '{self.db_name}'")
        
        return self

    @field_validator("db_engine")
    def validate_db_engine(cls, value: str) -> str:
        supported_engines = ["postgresql"]
        
        if value not in supported_engines:
            log.error(f"❌ Engine '{value}' is not supported! Supported engines: {supported_engines}")
            raise DatabaseValueError(f"Unsupported database engine: {value}")
            
        log.info(f"✅ Selected {value} database engine")
        return value

    @field_validator("db_name")
    def validate_db_name(cls, value: str) -> str:
        if not value or not isinstance(value, str):
            log.error("❌ Database name must be a non-empty string")
            raise DatabaseValueError("Database name must be a non-empty string")
        
        if not re.match(r'^[a-z][a-z0-9_]{0,62}$', value):
            log.error(f"❌ Database name '{value}' contains invalid characters. Use only lowercase letters, numbers, and underscores")
            raise DatabaseValueError(f"Invalid database name format: {value}")
        
        log.info(f"✅ Database name: {value}")
        return value

    @field_validator("db_host")
    def validate_db_host(cls, value: str) -> str:
        if not value or not isinstance(value, str):
            log.error("❌ Database host must be a non-empty string")
            raise DatabaseValueError("Database host must be a non-empty string")
        
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        hostname_pattern = r'^[a-zA-Z0-9][a-zA-Z0-9\.\-]{0,255}$'
        
        if not (re.match(ip_pattern, value) or re.match(hostname_pattern, value)):
            log.error(f"❌ Database host '{value}' has invalid format")
            raise DatabaseInvalidError(f"Invalid database host format: {value}")
        
        log.info(f"✅ Database host: {value}")
        return value

    @field_validator("db_port")
    def validate_db_port(cls, value: int) -> int:
        if not isinstance(value, int):
            log.error("❌ Database port must be an integer")
            raise DatabaseValueError("Database port must be an integer")
        
        if not 1 <= value <= 65535:
            log.error(f"❌ Database port {value} is out of valid range (1-65535)")
            raise DatabaseValueError(f"Database port must be between 1 and 65535")
        
        if value != 5432:
            log.info(f"⚠️ Using non-standard PostgreSQL port: {value}")
        else:
            log.info(f"✅ Database port: {value}")
        
        return value

    @field_validator("db_user")
    def validate_db_user(cls, value: str) -> str:
        if not value or not isinstance(value, str):
            log.error("❌ Database user must be a non-empty string")
            raise DatabaseValueError("Database user must be a non-empty string")
        
        if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]{0,62}$', value):
            log.error(f"❌ Database user '{value}' contains invalid characters. Use only letters, numbers, and underscores")
            raise DatabaseValueError(f"Invalid database username format: {value}")
        
        log.info(f"✅ Database user: {value}")
        return value

    @field_validator("db_password")
    def validate_db_password(cls, value: SecretStr) -> SecretStr:
        if not value or not value.get_secret_value():
            log.error("❌ Database password cannot be empty")
            raise DatabaseValueError("Database password cannot be empty")
        
        password = value.get_secret_value()
        
        if len(password) < 8:
            log.warning("⚠️ Database password is less than 8 characters long")
        
        log.info("✅ Database password set")
        return value

    @property
    def dsn(self) -> str:
        password = self.db_password.get_secret_value() if self.db_password else ""
        return f"postgresql+asyncpg://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def sync_dsn(self) -> str:
        password = self.db_password.get_secret_value() if self.db_password else ""
        return f"postgresql://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}"

    def get_connection_params(self) -> dict:
        return {
            "host": self.db_host,
            "port": self.db_port,
            "user": self.db_user,
            "password": self.db_password.get_secret_value() if self.db_password else "",
            "database": self.db_name,
        }
    
@lru_cache()
def get_database_config() -> _DatabaseConfig:
    return _DatabaseConfig()

try:
    database = get_database_config()
    log.success("✅ Database config initialized successfully")
except Exception as e:
    log.error(f"❌ Failed to initialize Database config: {e}")
    raise

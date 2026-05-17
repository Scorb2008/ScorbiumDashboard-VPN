from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr, field_validator
from typing import ClassVar, List, Literal
from functools import lru_cache
import re

from app.utils.path import env_file
from app.core.exceptions import TelegramException
from app.utils.log import log


class _TelegramConfig(BaseSettings):
    """
    Configuration for Telegram bot
    Parameters:
    - TELEGRAM_BOT_TOKEN: Telegram bot token from @BotFather
    - TELEGRAM_ADMIN_IDS: List of admin user IDs
    - TELEGRAM_TYPE_PROTOCOL: Protocol type (long/webhook)
    """
    
    TELEGRAM_TOKEN_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^\d+:[A-Za-z0-9_-]+$")

    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )

    telegram_bot_token: SecretStr = Field(
        default=...,
        description="Telegram bot token from @BotFather",
        validation_alias="TELEGRAM_BOT_TOKEN",
    )
    telegram_admin_ids: List[int] = Field(
        default_factory=list,
        description="List of admin user IDs",
        validation_alias="TELEGRAM_ADMIN_IDS",
    )
    telegram_type_protocol: Literal["webhook", "long"] = Field(
        default="long",
        description="Protocol type (long/webhook)",
        validation_alias="TELEGRAM_TYPE_PROTOCOL",
    )
    telegram_webhook_url: str = Field(
        default="",
        description="Full webhook URL, e.g. https://example.com/webhook/bot",
        validation_alias="TELEGRAM_WEBHOOK_URL",
    )
    telegram_webhook_path: str = Field(
        default="/webhook/bot",
        description="Path for webhook handler",
        validation_alias="TELEGRAM_WEBHOOK_PATH",
    )
    telegram_webhook_port: int = Field(
        default=8080,
        description="Port for aiohttp webhook server",
        validation_alias="TELEGRAM_WEBHOOK_PORT",
    )
    telegram_client_id: int = Field(
        default=0,
        description="Telegram OIDC Client ID from @BotFather",
        validation_alias="TELEGRAM_CLIENT_ID",
    )
    telegram_client_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Telegram OIDC Client Secret from @BotFather",
        validation_alias="TELEGRAM_CLIENT_SECRET",
    )
    telegram_bot_username: str = Field(
        default="",
        description="Bot username for legacy Telegram login widget, without @",
        validation_alias="TELEGRAM_BOT_USERNAME",
    )

    @field_validator("telegram_bot_token")
    @classmethod
    def validate_telegram_token(cls, value: SecretStr) -> SecretStr:
        if not cls.TELEGRAM_TOKEN_PATTERN.fullmatch(value.get_secret_value()):
            raise TelegramException(
                "Invalid Telegram bot token format. Expected format: '123456:ABCdef...'"
            )
        return value

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value):
        if isinstance(value, str):
            if not value:
                return []
            try:
                return [int(id.strip()) for id in re.split(r"[,\s;]+", value) if id.strip()]
            except ValueError:
                raise TelegramException("Invalid TELEGRAM_ADMIN_IDS format")
        return value

    @field_validator("telegram_client_id", mode="before")
    @classmethod
    def parse_client_id(cls, value):
        if isinstance(value, str) and not value.strip():
            return 0
        return value

    @field_validator("telegram_bot_username", mode="before")
    @classmethod
    def parse_bot_username(cls, value):
        if value is None:
            return ""
        value = str(value).strip()
        if value.startswith("@"):
            value = value[1:]
        return value
    
    
@lru_cache()
def get_telegram_config() -> _TelegramConfig:
    return _TelegramConfig()

try:
    telegram = get_telegram_config()
    log.success("✅ Telegram config initialized successfully\n")
except Exception as e:
    log.error(f"❌ Failed to initialize Telegram config: {e} \n Error in {__file__}: {e}")

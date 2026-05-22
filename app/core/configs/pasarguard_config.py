from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Any, Dict, Optional, Tuple
from functools import lru_cache

from app.utils.path import env_file
from app.core.exceptions import EnvException, PasarguardAuthError
from app.utils.log import log


class _PasarGuardConfig(BaseSettings):
    """
    Configuration for Pasarguard API
    Parameters:
    - PASARGUARD_ADMIN_PANEL: URL of the Pasarguard admin panel (required)
    - PASARGUARD_ADMIN_LOGIN: Admin login for authentication (optional if API key is provided)
    - PASARGUARD_ADMIN_PASSWORD: Admin password for authentication (optional if API key is provided)
    - PASARGUARD_API_KEY: API key for authentication (optional if login/password is provided)
    At least one authentication method must be provided: either login/password or API key.
    """

    model_config = SettingsConfigDict(
        env_file=env_file,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
        frozen=True,
    )

    pasarguard_admin_panel: HttpUrl = Field(
        default=...,
        validation_alias="PASARGUARD_ADMIN_PANEL",
        description="URL Admin panel Pasarguard",
    )

    pasarguard_admin_login: Optional[str] = Field(
        default=None,
        validation_alias="PASARGUARD_ADMIN_LOGIN",
        description="login (for auth on login/password)",
    )

    pasarguard_admin_password: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PASARGUARD_ADMIN_PASSWORD",
        description="Admin Password (for auth on login/password)",
    )

    pasarguard_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias="PASARGUARD_API_KEY",
        description="API Key for auth",
    )

    @field_validator("pasarguard_admin_panel")
    @classmethod
    def validate_admin_panel_url(cls, value: HttpUrl) -> HttpUrl:
        """Validate URL Admin panel"""

        # Reject bind-address values; Pasarguard panel URL must be dialable.
        if value.host == "0.0.0.0":  # nosec B104
            raise EnvException("⚠ PASARGUARD_ADMIN_PANEL cannot point to 0.0.0.0")

        if value.host in ["localhost", "127.0.0.1"]:
            log.warning(f"⚠️ Using localhost for admin panel: {value}")

        if value.scheme == "http" and value.host not in ["localhost", "127.0.0.1"]:
            log.warning(f"⚠️ Admin panel URL uses HTTP (not secure): {value}")

        return value

    @model_validator(mode="after")
    def validate_authentication_method(self) -> "_PasarGuardConfig":

        if not (self.has_password_auth or self.has_api_key):
            raise PasarguardAuthError(
                "At least one authentication method must be specified:\n"
                "- Login and password (PASARGUARD_ADMIN_LOGIN + PASARGUARD_ADMIN_PASSWORD)\n"
                "- API key (PASARGUARD_API_KEY)"
            )

        if self.has_password_auth and self.has_api_key:
            log.info("🔐 Using both: login/password and API Key")
        elif self.has_password_auth:
            log.info("🔐 Using: login/password")
        elif self.has_api_key:
            log.info("🔐 Using: API Key")

        return self

    @field_validator("pasarguard_admin_login")
    @classmethod
    def validate_username(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and len(value.strip()) == 0:
            raise EnvException("⚠ 'pasarguard_admin_login' cannot be empty!")
        return value

    def get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API requests"""
        headers: Dict[str, str] = {}

        if self.pasarguard_api_key:
            headers["X-API-Key"] = self.pasarguard_api_key.get_secret_value()
            log.debug("Using API key authentication for headers")
        elif self.has_password_auth:
            log.debug("Password authentication will be used for token acquisition")

        return headers

    def get_auth_data(self) -> Dict[str, str]:
        """Get authentication data for login requests"""
        auth_data: Dict[str, str] = {}

        if self.pasarguard_admin_login and self.pasarguard_admin_password:
            auth_data = {
                "username": self.pasarguard_admin_login,
                "password": self.pasarguard_admin_password.get_secret_value(),
            }
            log.debug("Using password authentication data")

        return auth_data

    @property
    def has_password_auth(self) -> bool:
        """Check if password authentication is available"""
        return bool(
            self.pasarguard_admin_login
            and self.pasarguard_admin_password
            and self.pasarguard_admin_password.get_secret_value().strip()
        )

    @property
    def has_api_key(self) -> bool:
        """Check if API key authentication is available"""
        try:
            return bool(
                self.pasarguard_api_key
                and self.pasarguard_api_key.get_secret_value().strip()
            )
        except Exception:
            return False

    def assert_login_credentials(self) -> Tuple[str, SecretStr]:
        if (
            self.pasarguard_admin_login is None
            or self.pasarguard_admin_password is None
        ):
            raise EnvException("❌ Login credentials are not properly configured!")

        return self.pasarguard_admin_login, self.pasarguard_admin_password

    def get_api_client_config(self) -> Dict[str, Any]:
        """
        Get complete configuration for API client

        Returns:
            Dictionary with base_url and authentication configuration
        """
        config = {
            "base_url": str(self.pasarguard_admin_panel),
            "auth_method": None,
        }
        if self.has_api_key:
            config["auth_method"] = "api_key"
            config["api_key"] = self.pasarguard_api_key
        elif self.has_password_auth:
            config["auth_method"] = "password"
            config["login"] = self.pasarguard_admin_login
            config["password"] = self.pasarguard_admin_password

        return config

    def __str__(self) -> str:
        auth_methods = []
        if self.has_password_auth:
            auth_methods.append("🔑 Password")
        if self.has_api_key:
            auth_methods.append("🔐 API Key")

        return (
            f"PasarGuardConfig(\n"
            f"  URL: {self.pasarguard_admin_panel}\n"
            f"  Auth: {', '.join(auth_methods) if auth_methods else '❌ None'}\n"
            f")"
        )


@lru_cache()
def get_pasarguard_config() -> Optional["_PasarGuardConfig"]:
    """Returns Marzban/Pasarguard config."""
    return _PasarGuardConfig()


try:
    pasarguard = get_pasarguard_config()
    if pasarguard:
        log.success("✅ Pasarguard config initialized successfully")
        log.debug(f"Pasarguard: {pasarguard}")
except EnvException as e:
    log.error(f"""
            ❌ Failed to initialize Pasarguard config: {e}
            Check .env file. The following must be specified:
            PASARGUARD_ADMIN_PANEL=https://your-panel.com
            And either:
            - PASARGUARD_ADMIN_LOGIN + PASARGUARD_ADMIN_PASSWORD
            - PASARGUARD_API_KEY
              """)
    raise

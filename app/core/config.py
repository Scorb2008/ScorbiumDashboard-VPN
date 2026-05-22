from typing import Dict, Optional
from typing_extensions import Any
from functools import lru_cache

from app.utils.log import log


class _Config:
    initialized: bool = False
    web_config: Optional[Any] = None
    telegram_config: Optional[Any] = None
    pasarguard_config: Optional[Any] = None
    database_config: Optional[Any] = None
    yookassa_config: Optional[Any] = None
    utils_config: Optional[Any] = None

    model_config = {
        "arbitrary_types_allowed": True,
        "validate_assignment": True,
    }

    @property
    def web(self) -> Any:
        if self.web_config is None:
            from .configs import web_config

            self.web_config = web_config
        return self.web_config

    @property
    def telegram(self) -> Any:
        if self.telegram_config is None:
            from .configs import telegram

            self.telegram_config = telegram
        return self.telegram_config

    @property
    def pasarguard(self) -> Any:
        if self.pasarguard_config is None:
            from .configs import pasarguard

            self.pasarguard_config = pasarguard
        return self.pasarguard_config

    @property
    def database(self) -> Any:
        if self.database_config is None:
            from .configs import database

            self.database_config = database
        return self.database_config

    @property
    def yookassa(self) -> Any:
        if self.yookassa_config is None:
            from .configs import yookassa

            self.yookassa_config = yookassa
        return self.yookassa_config

    @property
    def utils(self) -> Any:
        if self.utils_config is None:
            from .configs import utils

            self.utils_config = utils
        return self.utils_config

    def initialize(self, force: bool = False) -> Optional["_Config"]:
        if self.initialized and not force:
            return self

        log.info("Initializing all configs...")

        _ = self.telegram
        _ = self.pasarguard
        _ = self.database
        _ = self.yookassa
        _ = self.utils
        _ = self.web

        self.initialized = True
        return self

    def reload(self) -> Optional["_Config"]:
        log.info("Reloading all configs...")

        self.database_config = None
        self.pasarguard_config = None
        self.telegram_config = None
        self.yookassa_config = None
        self.utils_config = None
        self.web_config = None

        self.initialized = False
        return self.initialize(force=True)

    def validate_all(self) -> Dict[str, bool]:
        results = {}

        try:
            _ = self.web
            results["web"] = True
        except Exception as e:
            results["web"] = False
            log.error(f"Error validating Web settings: {e}")

        try:
            _ = self.database
            results["database"] = True
        except Exception as e:
            results["database"] = False
            log.error(f"Error validating database settings: {e}")

        try:
            _ = self.pasarguard
            results["pasarguard"] = True
        except Exception as e:
            results["pasarguard"] = False
            log.error(f"Error validating Pasarguard settings: {e}")

        try:
            _ = self.yookassa
            results["yookassa"] = True
        except Exception as e:
            results["yookassa"] = False
            log.error(f"Error validating Yookassa settings: {e}")

        try:
            _ = self.telegram
            results["telegram"] = True
        except Exception as e:
            results["telegram"] = False
            log.error(f"Error validating Telegram settings: {e}")

        try:
            _ = self.utils
            results["utils"] = True
        except Exception as e:
            results["utils"] = False
            log.error(f"Error validating utils settings: {e}")

        return results

    def __repr__(self):
        return (
            f"<Config: (telegram: {self.telegram}, pasarguard: {self.pasarguard}, "
            f"database: {self.database}, yookassa: {self.yookassa}, web: {self.web})>"
        )


@lru_cache()
def get_config() -> _Config:
    return _Config()


try:
    config = get_config()
    config.initialize()
    log.success("✅ All configs initialized successfully")
except Exception as e:
    log.error(f"❌ Failed to initialize configs: {e}. \n Error in {__file__}: {e}")
    raise

class AppException(Exception):
    pass


# ====== Basic Exception ======
class WebException(AppException):
    pass


class DatabaseException(AppException):
    pass


class PasarguardException(AppException):
    pass


class TelegramException(AppException):
    pass


class YookassaException(AppException):
    pass


class ConfigException(AppException):
    pass


class EnvException(AppException):
    pass


# ====== DatabaseException ======
class DatabaseValueError(DatabaseException):
    pass


class DatabaseConnectionError(DatabaseException):
    pass


class DatabaseInvalidError(DatabaseException):
    pass


class DatabaseNotFoundError(DatabaseException):
    pass


# ====== PasarguardException ======
class PasarguardAuthError(PasarguardException):
    pass


class PasarguardRequestError(PasarguardException):
    pass


class PasarguardValueError(PasarguardException):
    pass


# ====== TelegramException ======
class TelegramSendMessageError(TelegramException):
    pass


class TelegramAuthError(TelegramException):
    pass


# ====== YookassaException ======
class YookassaPaymentError(YookassaException):
    pass


class YookassaValueError(YookassaException):
    pass


class YookassaWebhookError(YookassaException):
    pass


# ====== ConfigException ======
class ConfigInitError(ConfigException):
    pass


class ConfigValueError(ConfigException):
    pass


class ConfigNotFoundError(ConfigException):
    pass

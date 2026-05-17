import asyncio
import uuid
from decimal import Decimal
from typing import Optional

import yookassa
from yookassa import Payment as YKPayment
from yookassa.domain.response import PaymentResponse

from app.core.exceptions import YookassaPaymentError
from app.utils.log import log


async def _get_yookassa_credentials() -> Optional[dict]:
    """
    Returns credentials Yookassa.
    Source of truth: bot_settings (DB).
    Use ORM — SQL-injection impossible.
    """
    try:
        from app.core.database import AsyncSessionFactory
        from app.services.bot_settings import BotSettingsService
        async with AsyncSessionFactory() as session:
            svc = BotSettingsService(session)
            shop_id_str = await svc.get("yookassa_shop_id_override") or ""
            secret_key = await svc.get("yookassa_secret_key_override") or ""
            if shop_id_str and secret_key:
                return {"shop_id": int(shop_id_str), "secret_key": secret_key}
    except Exception as e:
        log.debug(f"YooKassa DB credentials lookup failed: {e}")
    return None


_yookassa_lock = asyncio.Lock()


def _configure_yookassa_sync(shop_id: int, secret_key: str) -> None:
    """Configure YooKassa synchronously. Caller must hold _yookassa_lock."""
    yookassa.Configuration.account_id = shop_id
    yookassa.Configuration.secret_key = secret_key


class YookassaService:
    def __init__(self, shop_id: Optional[int] = None, secret_key: Optional[str] = None) -> None:
        if shop_id and secret_key:
            _configure_yookassa_sync(shop_id, secret_key)
            self._ready = True
        else:
            self._ready = False
            raise YookassaPaymentError("Yookassa is not configured in bot settings.")

    @classmethod
    async def create(cls) -> "YookassaService":
        """Async factory — get settings from bot settings (DB)."""
        creds = await _get_yookassa_credentials()
        if not creds:
            raise YookassaPaymentError("Yookassa is not configured.")
        async with _yookassa_lock:
            return cls(shop_id=creds["shop_id"], secret_key=creds["secret_key"])

    async def create_payment(
        self,
        amount: Decimal,
        description: str,
        return_url: str,
        currency: str = "RUB",
        metadata: Optional[dict] = None,
        payment_method: Optional[str] = None,
    ) -> PaymentResponse:
        try:
            data: dict = {
                "amount": {"value": str(amount), "currency": currency},
                "confirmation": {"type": "redirect", "return_url": return_url},
                "capture": True,
                "description": description,
                "metadata": metadata or {},
            }
            if payment_method:
                data["payment_method_data"] = {"type": payment_method}
            payment = await asyncio.wait_for(
                asyncio.to_thread(YKPayment.create, data, idempotency_key=str(uuid.uuid4())),
                timeout=30,
            )
            log.info("Yookassa payment created: %s", payment.id)
            return payment
        except asyncio.TimeoutError:
            log.error("Yookassa payment creation timed out")
            raise YookassaPaymentError("Payment service timed out. Please try again.")
        except Exception as e:
            log.error("Yookassa payment creation failed: %s", e)
            raise YookassaPaymentError("Payment service unavailable. Please try again.")

    async def create_sbp_payment(
        self,
        amount: Decimal,
        description: str,
        return_url: str,
        metadata: Optional[dict] = None,
    ) -> PaymentResponse:
        return await self.create_payment(
            amount=amount,
            description=description,
            return_url=return_url,
            metadata=metadata,
            payment_method="sbp",
        )

    async def get_payment(self, payment_id: str) -> PaymentResponse:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(YKPayment.find_one, payment_id),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log.error("Yookassa payment lookup timed out: %s", payment_id)
            raise YookassaPaymentError("Payment service timed out.")
        except Exception as e:
            log.error("Yookassa payment lookup failed: %s", e)
            raise YookassaPaymentError("Payment service unavailable.")

    @staticmethod
    async def _sync_get_payment(payment_id: str) -> PaymentResponse:
        """Sync payment checker — used in async context through await YookassaService.create()."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(YKPayment.find_one, payment_id),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log.error("Yookassa payment lookup timed out: %s", payment_id)
            raise YookassaPaymentError("Payment service timed out.")
        except Exception as e:
            log.error("Yookassa payment lookup failed: %s", e)
            raise YookassaPaymentError("Payment service unavailable.")

    async def is_succeeded(self, payment_id: str) -> bool:
        payment = await self.get_payment(payment_id)
        return payment.status == "succeeded"

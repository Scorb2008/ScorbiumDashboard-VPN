"""
BasePaymentService — abstract interface for all payment providers.

All payment services (YooKassa, CryptoBot, FreeKassa, Platega, PayPalych, etc.)
must implement this interface to ensure consistent behavior and enable
future provider additions without changing calling code.
"""
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional, Dict, Any


class BasePaymentService(ABC):
    """Abstract base class for payment provider integrations."""

    @abstractmethod
    async def create_payment(
        self,
        amount: Decimal,
        description: str,
        external_id: str,
        currency: str = "RUB",
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Create a payment and return provider response.

        Returns dict with keys:
            - ok: bool
            - payment_id: str (external provider ID)
            - confirmation_url: Optional[str] (redirect URL for user)
            - raw: Optional[Any] (raw provider response)
        """
        ...

    @abstractmethod
    async def get_payment_status(self, payment_id: str) -> Dict[str, Any]:
        """
        Check payment status with provider.

        Returns dict with keys:
            - ok: bool
            - status: str ("succeeded" | "pending" | "failed" | "canceled")
            - raw: Optional[Any]
        """
        ...

    @abstractmethod
    async def refund_payment(self, payment_id: str) -> Dict[str, Any]:
        """
        Refund a completed payment.

        Returns dict with keys:
            - ok: bool
            - message: str
        """
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if provider credentials are configured."""
        ...

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """
        Test API connectivity.

        Returns dict with keys:
            - ok: bool
            - message: str
        """
        ...

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .utils import is_safe_https_url


class PlisioError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlisioInvoice:
    transaction_id: str
    invoice_url: str
    status: str = "new"


class PlisioClient:
    """Small adapter for Plisio's official invoice API.

    Telegram updates still use getUpdates. This adapter only polls the external
    payment provider, so no Telegram webhook is created.
    """

    def __init__(
        self,
        api_key: str,
        *,
        currency: str = "USDT_TRX",
        source_currency: str = "IRR",
        source_amount_multiplier: int = 10,
        api_base: str = "https://api.plisio.net/api/v1",
        timeout_seconds: int = 20,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("Plisio API key is required")
        if source_amount_multiplier <= 0:
            raise ValueError("source_amount_multiplier must be positive")
        self._api_key = api_key
        self.currency = currency
        self.source_currency = source_currency
        self.source_amount_multiplier = source_amount_multiplier
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._owns_session = session is None
        self.session = session or requests.Session()

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "api_key": self._api_key}
        transport_failed = False
        try:
            response = self.session.get(
                f"{self.api_base}/{path.lstrip('/')}",
                params=query,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            # The provider uses a query-string API key, which requests may
            # percent-encode in exception URLs. Raw replacement therefore is
            # not sufficient; expose no transport URL/detail and suppress the
            # original exception chain completely.
            transport_failed = True
        if transport_failed:
            # Raise after leaving ``except`` so even ``__context__`` cannot
            # retain a requests exception containing encoded credentials.
            raise PlisioError("Plisio request failed")
        if payload.get("status") != "success":
            # Provider-controlled error fields have echoed request query
            # values in practice.  The API key is sent in that query, so
            # never expose the remote message through exceptions or logs.
            raise PlisioError("Plisio rejected the request")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise PlisioError("Plisio returned an unexpected response")
        return data

    def create_invoice(
        self,
        *,
        order_number: str,
        order_name: str,
        amount_in_shop_currency: int,
        expire_minutes: int = 30,
        description: str = "",
        callback_url: str = "",
    ) -> PlisioInvoice:
        if amount_in_shop_currency <= 0:
            raise ValueError("invoice amount must be positive")
        params: dict[str, Any] = {
            "source_currency": self.source_currency,
            "source_amount": amount_in_shop_currency * self.source_amount_multiplier,
            "order_number": order_number,
            "order_name": order_name,
            "currency": self.currency,
            "allowed_psys_cids": self.currency,
            "expire_min": expire_minutes,
            "return_existing": 1,
        }
        if description:
            params["description"] = description
        if callback_url:
            separator = "&" if "?" in callback_url else "?"
            params["callback_url"] = f"{callback_url}{separator}json=true"
        data = self._get("invoices/new", params)
        txn_id = str(data.get("txn_id") or "")
        invoice_url = str(data.get("invoice_url") or "")
        if not txn_id or not invoice_url:
            raise PlisioError("Plisio response has no transaction ID or invoice URL")
        if not is_safe_https_url(invoice_url):
            raise PlisioError("Plisio returned an unsafe invoice URL")
        return PlisioInvoice(txn_id, invoice_url, str(data.get("status") or "new"))

    def operation(self, transaction_id: str) -> dict[str, Any]:
        if not transaction_id:
            raise ValueError("transaction_id is required")
        return self._get(f"operations/{transaction_id}", {})

    def is_completed(self, transaction_id: str) -> bool:
        return str(self.operation(transaction_id).get("status") or "").lower() == "completed"

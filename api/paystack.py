"""
Thin wrapper around the Paystack Transactions API.
Docs: https://paystack.com/docs/api/transaction/
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger('api')

BASE_URL = "https://api.paystack.co"
TIMEOUT = 15


class PaystackError(Exception):
    pass


def _headers():
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackError("PAYSTACK_SECRET_KEY is not configured.")
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def initialize_transaction(email, amount_naira, reference, callback_url, metadata=None):
    """
    Kicks off a Paystack transaction. Amount must be converted to kobo
    (Paystack's smallest unit) before sending.
    """
    payload = {
        "email": email,
        "amount": int(amount_naira * 100),
        "reference": reference,
        "callback_url": callback_url,
        "currency": "NGN",
        "metadata": metadata or {},
    }
    try:
        resp = requests.post(f"{BASE_URL}/transaction/initialize", json=payload, headers=_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Paystack initialize failed: %s", exc)
        raise PaystackError("Could not reach Paystack. Please try again shortly.") from exc

    data = resp.json()
    if not data.get("status"):
        raise PaystackError(data.get("message", "Paystack rejected the request."))
    return data["data"]  # contains authorization_url, access_code, reference


def verify_transaction(reference):
    try:
        resp = requests.get(f"{BASE_URL}/transaction/verify/{reference}", headers=_headers(), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Paystack verify failed: %s", exc)
        raise PaystackError("Could not verify payment with Paystack right now.") from exc

    data = resp.json()
    if not data.get("status"):
        raise PaystackError(data.get("message", "Paystack could not find that transaction."))
    return data["data"]  # contains status ('success'/'failed'), amount, reference, customer, etc.

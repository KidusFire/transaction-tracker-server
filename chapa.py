"""
Thin wrapper around Chapa's payment API (https://developer.chapa.co/).
Reads credentials from environment variables — never hardcode real keys here.
"""

import os
import requests

CHAPA_SECRET_KEY = os.getenv("CHAPA_SECRET_KEY", "")
CHAPA_BASE_URL = "https://api.chapa.co/v1"


def initialize_checkout(amount_etb: float, tx_ref: str, customer_email: str,
                         first_name: str, callback_url: str, return_url: str,
                         title: str, description: str) -> dict:
    """
    Starts a Chapa checkout session. Returns Chapa's response dict —
    on success, response["data"]["checkout_url"] is where to send the customer.
    Raises requests.HTTPError if Chapa rejects the request.
    """
    headers = {"Authorization": f"Bearer {CHAPA_SECRET_KEY}"}
    payload = {
        "amount": str(amount_etb),
        "currency": "ETB",
        "email": customer_email,
        "first_name": first_name,
        "tx_ref": tx_ref,
        "callback_url": callback_url,
        "return_url": return_url,
        "customization[title]": title[:16],  # Chapa limits this field's length
        "customization[description]": description,
    }
    response = requests.post(f"{CHAPA_BASE_URL}/transaction/initialize", headers=headers, json=payload, timeout=15)
    response.raise_for_status()
    return response.json()


def verify_transaction(tx_ref: str) -> dict:
    """Asks Chapa directly whether a transaction actually succeeded — the source of truth,
    used both by the webhook handler and as a manual fallback if a webhook is delayed."""
    headers = {"Authorization": f"Bearer {CHAPA_SECRET_KEY}"}
    response = requests.get(f"{CHAPA_BASE_URL}/transaction/verify/{tx_ref}", headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()

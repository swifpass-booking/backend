"""eSewa (ePay v2) and Khalti (ePayment v2) sandbox integrations.

Both are redirect flows: the booking is created `pending_payment` (seats reserved), the browser is
sent to the gateway, the gateway sends it back to the frontend, and the frontend calls
POST /v1/payments/verify — which re-checks the payment *server-to-server* with the gateway
before the booking is confirmed. Nothing the browser says is trusted on its own.
"""
import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


class GatewayError(Exception):
    pass


def _http_json(url, *, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise GatewayError(f"Gateway returned HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise GatewayError(f"Could not reach the payment gateway: {e}")


def _rupees(minor: int) -> str:
    return str(minor // 100) if minor % 100 == 0 else f"{minor / 100:.2f}"


# ---- eSewa ------------------------------------------------------------------
def _esewa_sign(message: str) -> str:
    digest = hmac.new(settings.ESEWA_SECRET_KEY.encode(), message.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def esewa_start(booking, transaction_uuid: str) -> dict:
    if not settings.ESEWA_SECRET_KEY or not settings.ESEWA_PRODUCT_CODE:
        raise GatewayError("eSewa is not configured (ESEWA_PRODUCT_CODE / ESEWA_SECRET_KEY).")
    amount = _rupees(booking.total_minor)
    fields = {
        "amount": amount,
        "tax_amount": "0",
        "total_amount": amount,
        "transaction_uuid": transaction_uuid,
        "product_code": settings.ESEWA_PRODUCT_CODE,
        "product_service_charge": "0",
        "product_delivery_charge": "0",
        "success_url": f"{settings.FRONTEND_BASE_URL}/payment/esewa",
        "failure_url": f"{settings.FRONTEND_BASE_URL}/payment/esewa?failed=1&ref={transaction_uuid}",
        "signed_field_names": "total_amount,transaction_uuid,product_code",
    }
    fields["signature"] = _esewa_sign(
        f"total_amount={fields['total_amount']},transaction_uuid={transaction_uuid},product_code={fields['product_code']}"
    )
    return {"provider": "esewa", "method": "POST", "url": settings.ESEWA_FORM_URL, "fields": fields}


def esewa_check(data_b64: str):
    """Validate the signed callback payload, then confirm with eSewa's status API.
    Returns (transaction_uuid, total_minor) once eSewa itself reports COMPLETE."""
    try:
        payload = json.loads(base64.b64decode(data_b64 + "=" * (-len(data_b64) % 4)))
    except (ValueError, TypeError):
        raise GatewayError("Unreadable eSewa response.")
    names = str(payload.get("signed_field_names", "")).split(",")
    message = ",".join(f"{n}={payload.get(n)}" for n in names)
    if not hmac.compare_digest(_esewa_sign(message), str(payload.get("signature", ""))):
        raise GatewayError("eSewa response signature did not match.")
    txn, total = payload.get("transaction_uuid"), str(payload.get("total_amount", "")).replace(",", "")
    query = urllib.parse.urlencode({"product_code": settings.ESEWA_PRODUCT_CODE, "total_amount": total, "transaction_uuid": txn})
    status = _http_json(f"{settings.ESEWA_STATUS_URL}?{query}")
    if status.get("status") != "COMPLETE":
        raise GatewayError(f"eSewa reports the payment as {status.get('status', 'unknown')}.")
    return txn, round(float(total) * 100)


# ---- Khalti -----------------------------------------------------------------
def _khalti_headers():
    if not settings.KHALTI_SECRET_KEY:
        raise GatewayError("Khalti sandbox is not configured — set KHALTI_SECRET_KEY (see test-admin.khalti.com).")
    return {"Authorization": f"Key {settings.KHALTI_SECRET_KEY}"}


def khalti_start(booking) -> dict:
    if booking.total_minor < 1000:
        raise GatewayError("Khalti needs a total of at least Rs 10.")
    res = _http_json(
        f"{settings.KHALTI_BASE_URL}/epayment/initiate/",
        method="POST",
        headers=_khalti_headers(),
        body={
            "return_url": f"{settings.FRONTEND_BASE_URL}/payment/khalti",
            "website_url": settings.FRONTEND_BASE_URL,
            "amount": booking.total_minor,
            "purchase_order_id": booking.reference,
            "purchase_order_name": booking.title[:100],
            "customer_info": {
                "name": (booking.passengers.first().full_name if booking.passengers.exists() else "Traveller"),
                "email": booking.contact_email or "traveller@example.com",
                "phone": booking.contact_phone.lstrip("+")[-10:],
            },
        },
    )
    if not res.get("pidx") or not res.get("payment_url"):
        raise GatewayError("Khalti did not return a payment link.")
    return {"provider": "khalti", "method": "GET", "url": res["payment_url"], "pidx": res["pidx"]}


def khalti_check(pidx: str):
    """Lookup with Khalti; returns (status, total_minor)."""
    res = _http_json(f"{settings.KHALTI_BASE_URL}/epayment/lookup/", method="POST", headers=_khalti_headers(), body={"pidx": pidx})
    return res.get("status"), int(res.get("total_amount") or 0)

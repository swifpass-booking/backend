"""eSewa (ePay v2), Khalti (ePayment v2), Fonepay (dev) and Stripe (test mode) integrations.

Both are redirect flows: the booking is created `pending_payment` (seats reserved), the browser is
sent to the gateway, the gateway sends it back to the frontend, and the frontend calls
POST /v1/payments/verify — which re-checks the payment *server-to-server* with the gateway
before the booking is confirmed. Nothing the browser says is trusted on its own.
"""
import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings


class GatewayError(Exception):
    pass


def _http_json(url, *, method="GET", body=None, headers=None, form=None):
    data = json.dumps(body).encode() if body is not None else None
    content_type = "application/json"
    if form is not None:  # Stripe's API takes form-encoded bodies
        data, content_type = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": content_type, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
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


def esewa_status(txn, total) -> str | None:
    """eSewa's own record of one payment (COMPLETE, CANCELED, PENDING, ...)."""
    query = urllib.parse.urlencode({"product_code": settings.ESEWA_PRODUCT_CODE, "total_amount": total, "transaction_uuid": txn})
    return _http_json(f"{settings.ESEWA_STATUS_URL}?{query}").get("status")


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
    status = esewa_status(txn, total)
    if status != "COMPLETE":
        raise GatewayError(f"eSewa reports the payment as {status or 'unknown'}.")
    return txn, round(float(total) * 100)


# ---- Khalti -----------------------------------------------------------------
def _khalti_headers():
    if not settings.KHALTI_SECRET_KEY:
        raise GatewayError("Khalti sandbox is not configured — set KHALTI_SECRET_KEY (see test-admin.khalti.com).")
    return {"Authorization": f"Key {settings.KHALTI_SECRET_KEY}"}


def _khalti_demo() -> bool:
    # shortcut: with no sandbox key, DEMO_INSTANT_PAYMENT fakes Khalti locally (settings.py keeps it off in production)
    return settings.DEMO_INSTANT_PAYMENT and not settings.KHALTI_SECRET_KEY


def khalti_start(booking) -> dict:
    if _khalti_demo():
        pidx = f"demo-{booking.reference}"
        return {"provider": "khalti", "method": "GET", "url": f"{settings.FRONTEND_BASE_URL}/payment/khalti?pidx={pidx}", "pidx": pidx}
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
    if _khalti_demo() and pidx.startswith("demo-"):
        from .models import Booking
        b = Booking.objects.filter(payment_ref=pidx).first()
        return "Completed", b.total_minor if b else 0
    res = _http_json(f"{settings.KHALTI_BASE_URL}/epayment/lookup/", method="POST", headers=_khalti_headers(), body={"pidx": pidx})
    return res.get("status"), int(res.get("total_amount") or 0)


# ---- Fonepay ----------------------------------------------------------------
def _fonepay_sign(message: str) -> str:
    return hmac.new(settings.FONEPAY_SECRET_KEY.encode(), message.encode(), hashlib.sha512).hexdigest()


def fonepay_start(booking, prn: str) -> dict:
    if not settings.FONEPAY_MERCHANT_CODE or not settings.FONEPAY_SECRET_KEY:
        raise GatewayError("Fonepay is not configured (FONEPAY_MERCHANT_CODE / FONEPAY_SECRET_KEY).")
    fields = {
        "PID": settings.FONEPAY_MERCHANT_CODE,
        "MD": "P",
        "PRN": prn,
        "AMT": _rupees(booking.total_minor),
        "CRN": "NPR",
        "DT": time.strftime("%m/%d/%Y"),
        "R1": "SwiftPass",
        "R2": "N/A",
        "RU": f"{settings.FRONTEND_BASE_URL}/payment/fonepay",
    }
    fields["DV"] = _fonepay_sign(",".join(fields[k] for k in ("PID", "MD", "PRN", "AMT", "CRN", "DT", "R1", "R2", "RU")))
    return {"provider": "fonepay", "method": "GET", "url": f"{settings.FONEPAY_URL}?{urllib.parse.urlencode(fields)}"}


def fonepay_check(q: dict):
    """Validate the signed return query. Returns (prn, paid_minor), paid_minor None when Fonepay says it failed.
    shortcut: trusts the HMAC-signed redirect, skips Fonepay's verificationMerchant call, add it before going live."""
    names = ("PRN", "PID", "PS", "RC", "UID", "BC", "INI", "P_AMT", "R_AMT")
    if not hmac.compare_digest(_fonepay_sign(",".join(str(q.get(n, "")) for n in names)), str(q.get("DV", ""))):
        raise GatewayError("Fonepay response signature did not match.")
    prn = str(q.get("PRN", ""))
    if str(q.get("PS")).lower() != "true" or str(q.get("RC")).lower() != "successful":
        return prn, None
    return prn, round(float(q.get("P_AMT") or 0) * 100)


# ---- Stripe (Checkout Sessions, test mode) ----------------------------------
# Hosted Checkout, same redirect shape as the others: the booking waits as pending_payment, the browser pays on
# stripe.com, and the payment is confirmed by re-fetching the session (verify_payment) or by the signed webhook.
# With no STRIPE_SECRET_KEY in DEBUG, STRIPE_SIMULATE fakes Stripe: sessions are `cs_sim_*` and the outcome
# (card declined, 3DS failed, expired, paid late, ...) is picked on the frontend simulator page.
def _stripe_headers():
    if not settings.STRIPE_SECRET_KEY:
        raise GatewayError("Stripe is not configured — set STRIPE_SECRET_KEY (a sk_test_ key from dashboard.stripe.com/test/apikeys).")
    return {"Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}"}


def stripe_start(booking) -> dict:
    if settings.STRIPE_SIMULATE:
        sid = f"cs_sim_{booking.reference}"
        return {"provider": "stripe", "method": "GET", "url": f"{settings.FRONTEND_BASE_URL}/payment/stripe/sim?session_id={sid}", "sessionId": sid}
    form = {
        "mode": "payment",
        "success_url": f"{settings.FRONTEND_BASE_URL}/payment/stripe?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{settings.FRONTEND_BASE_URL}/payment/stripe?failed=1",
        "client_reference_id": booking.reference,
        "metadata[booking_reference]": booking.reference,
        "payment_intent_data[metadata][booking_reference]": booking.reference,  # copied onto the charge: charge.refunded finds the booking
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": (settings.STRIPE_CURRENCY or booking.currency).lower(),
        "line_items[0][price_data][unit_amount]": booking.total_minor,
        "line_items[0][price_data][product_data][name]": booking.title[:100],
    }
    if booking.contact_email:
        form["customer_email"] = booking.contact_email
    res = _http_json(f"{settings.STRIPE_API_BASE}/v1/checkout/sessions", method="POST", headers=_stripe_headers(), form=form)
    if not res.get("id") or not res.get("url"):
        raise GatewayError("Stripe did not return a checkout link.")
    return {"provider": "stripe", "method": "GET", "url": res["url"], "sessionId": res["id"]}


def stripe_check(session_id: str):
    """Lookup with Stripe; returns ("paid" | "unpaid" | "expired", total_minor)."""
    if session_id.startswith("cs_sim_"):
        if not settings.STRIPE_SIMULATE:
            raise GatewayError("Simulated Stripe sessions are disabled.")
        from .models import Booking
        b = Booking.objects.filter(payment_ref=session_id).first()
        outcome = (b.gateway_info or {}).get("simOutcome") if b else None
        if outcome == "expired":
            return "expired", 0
        if outcome == "short":  # paid less than the total: exercises the amount check
            return "paid", b.total_minor - 100
        return ("paid", b.total_minor) if outcome == "paid" else ("unpaid", 0)
    res = _http_json(f"{settings.STRIPE_API_BASE}/v1/checkout/sessions/{urllib.parse.quote(session_id, safe='')}", headers=_stripe_headers())
    if res.get("status") == "expired":
        return "expired", 0
    return ("paid" if res.get("payment_status") == "paid" else "unpaid"), int(res.get("amount_total") or 0)


def stripe_event(payload: bytes, header: str, now=None) -> dict:
    """Verify a webhook's Stripe-Signature (HMAC-SHA256 over "<t>.<body>", 5-minute tolerance) and return the event."""
    if not settings.STRIPE_WEBHOOK_SECRET:
        raise GatewayError("STRIPE_WEBHOOK_SECRET is not set.")
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    stamp = next((v for k, v in parts if k == "t"), "")
    expected = hmac.new(settings.STRIPE_WEBHOOK_SECRET.encode(), stamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    if not stamp.isdigit() or abs((now or time.time()) - int(stamp)) > 300 or not any(
        hmac.compare_digest(expected, v) for k, v in parts if k == "v1"
    ):
        raise GatewayError("Stripe webhook signature did not match.")
    try:
        return json.loads(payload)
    except ValueError:
        raise GatewayError("Unreadable Stripe webhook body.")


def stripe_refund(booking) -> str:
    """Refund the whole payment and return the refund id. The idempotency key makes a retried click refund once."""
    ref = booking.payment_ref or ""
    if ref.startswith("cs_sim_"):
        if not settings.STRIPE_SIMULATE:
            raise GatewayError("Simulated Stripe sessions are disabled.")
        return f"re_sim_{booking.reference}"
    session = _http_json(f"{settings.STRIPE_API_BASE}/v1/checkout/sessions/{urllib.parse.quote(ref, safe='')}", headers=_stripe_headers())
    if not session.get("payment_intent"):
        raise GatewayError("Stripe has no payment to refund for this booking.")
    res = _http_json(
        f"{settings.STRIPE_API_BASE}/v1/refunds", method="POST",
        headers={**_stripe_headers(), "Idempotency-Key": f"refund-{booking.id}"},
        form={"payment_intent": session["payment_intent"], "amount": booking.total_minor, "metadata[booking_reference]": booking.reference},
    )
    if not res.get("id"):
        raise GatewayError("Stripe did not return a refund.")
    return res["id"]

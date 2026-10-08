import json
import re
import secrets
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.jwt import decode_token
from accounts.models import User
from accounts.throttle import throttle
from accounts.views import is_valid_email, is_valid_phone
from catalog.models import PLATFORM_FEE_RATE, Occurrence

from . import gateways
from .models import Booking, Passenger, Ticket

PAYMENT_METHODS = {"esewa", "khalti", "fonepay", "card"}
GATEWAY_METHODS = {"esewa", "khalti"}  # real redirect flow on web; other methods stay instant-confirm
MAX_PASSENGERS = 9


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def optional_user(request):
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else None
    if not token:
        return None
    user_id = decode_token(token)
    return User.objects.filter(id=user_id).first() if user_id else None


def _release(booking, new_status="cancelled"):
    """Give a never-paid booking's seats back."""
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        if booking.status != "pending_payment":
            return
        booking.status = new_status
        booking.save(update_fields=["status"])
        Occurrence.objects.filter(pk=booking.occurrence_id).update(
            seats_sold=F("seats_sold") - booking.passenger_count
        )


def _release_stale_pending():
    cutoff = timezone.now() - timedelta(minutes=settings.PENDING_PAYMENT_MINUTES)
    for b in Booking.objects.filter(status="pending_payment", created_at__lt=cutoff):
        _release(b)


def _start_gateway(booking):
    if booking.payment_method == "esewa":
        ref = f"{booking.reference}-{secrets.token_hex(3)}"
        info = gateways.esewa_start(booking, ref)
    else:
        info = gateways.khalti_start(booking)
        ref = info["pidx"]
    booking.payment_ref = ref
    booking.save(update_fields=["payment_ref"])
    return info


def _issue_tickets(booking):
    for passenger in booking.passengers.all():
        Ticket.objects.get_or_create(
            passenger=passenger, defaults={"booking": booking, "occurrence": booking.occurrence}
        )


def _with_gateway(booking, status):
    body = booking.to_dict()
    try:
        body["gateway"] = _start_gateway(booking)
    except gateways.GatewayError as e:
        _release(booking)
        return error(502, "gateway_unavailable", str(e))
    return JsonResponse(body, status=status)


@csrf_exempt
@require_http_methods(["POST"])
@throttle("booking", limit=20, window=600)
def create_booking(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return error(400, "validation_failed", "Malformed request body.")
    if not isinstance(body, dict):
        return error(400, "validation_failed", "Malformed request body.")

    offer = body.get("offer")
    names = body.get("names")
    phone = body.get("phone")
    if isinstance(phone, str):
        phone = re.sub(r"[\s\-()]", "", phone)
    email = body.get("email") or None
    payment = body.get("payment")
    total = body.get("total")

    if not isinstance(offer, dict) or not isinstance(offer.get("occurrenceId"), str):
        return error(400, "validation_failed", "An offer is required.")
    if (
        not isinstance(names, list)
        or not 1 <= len(names) <= MAX_PASSENGERS
        or not all(isinstance(n, str) and 1 <= len(n.strip()) <= 255 for n in names)
    ):
        return error(400, "validation_failed", f"Provide 1–{MAX_PASSENGERS} traveller names.")
    if not is_valid_phone(phone):
        return error(400, "validation_failed", "Enter a valid phone number.")
    if email is not None and not (isinstance(email, str) and is_valid_email(email)):
        return error(400, "validation_failed", "Enter a valid email address.")
    if payment not in PAYMENT_METHODS:
        return error(400, "validation_failed", "Choose a valid payment method.")
    if not isinstance(total, dict) or not isinstance(total.get("amount"), int):
        return error(400, "validation_failed", "A total amount is required.")

    user = optional_user(request)
    raw_key = request.headers.get("Idempotency-Key", "").strip()[:100]
    idem = f"{user.id if user else 'anon'}:{raw_key}" if raw_key else None
    if idem:
        existing = Booking.objects.filter(idempotency_key=idem).first()
        if existing:
            if existing.status == "pending_payment":
                return _with_gateway(existing, 200)
            return JsonResponse(existing.to_dict(), status=200)

    _release_stale_pending()
    via_gateway = payment in GATEWAY_METHODS and request.headers.get("X-Client") != "mobile"

    try:
        with transaction.atomic():
            # Price and availability come from the database, never from the client.
            occurrence = (
                Occurrence.objects.select_for_update()
                .select_related("service", "service__provider")
                .filter(id=offer["occurrenceId"], status="scheduled")
                .first()
            )
            if not occurrence:
                return error(400, "validation_failed", "That trip or event is no longer available.")

            n = len(names)
            if occurrence.seats_available() < n:
                return error(409, "seat_unavailable", "Not enough seats left.")

            unit_price = occurrence.base_price_minor
            unit_fee = round(unit_price * PLATFORM_FEE_RATE)
            subtotal, fees = unit_price * n, unit_fee * n
            if total["amount"] != subtotal + fees:
                return error(409, "price_changed", "The price changed. Review the new total and try again.")

            booking = Booking.objects.create(
                user=user,
                occurrence=occurrence,
                offer_id=f"ofr_{occurrence.id}",
                mode=occurrence.mode,
                title=occurrence.service.title,
                provider_name=occurrence.service.provider.display_name,
                departs_at=occurrence.departs_at,
                contact_phone=phone,
                contact_email=email,
                payment_method=payment,
                passenger_count=n,
                subtotal_minor=subtotal,
                fees_minor=fees,
                total_minor=subtotal + fees,
                currency="NPR",
                created_via="mobile" if request.headers.get("X-Client") == "mobile" else "web",
                idempotency_key=idem,
                status="pending_payment" if via_gateway else "confirmed",
            )
            for i, name in enumerate(names):
                passenger = Passenger.objects.create(booking=booking, full_name=name.strip(), is_lead=(i == 0), order=i)
                if not via_gateway:
                    Ticket.objects.create(booking=booking, passenger=passenger, occurrence=occurrence)

            Occurrence.objects.filter(pk=occurrence.pk).update(seats_sold=F("seats_sold") + n)
    except IntegrityError:
        # Concurrent retry with the same Idempotency-Key lost the race.
        existing = Booking.objects.filter(idempotency_key=idem).first() if idem else None
        if existing:
            return JsonResponse(existing.to_dict(), status=200)
        raise

    if via_gateway:
        return _with_gateway(booking, 201)
    return JsonResponse(booking.to_dict(), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@throttle("payment-verify", limit=30, window=600)
def verify_payment(request):
    """Called by the frontend when the gateway sends the traveller back. Re-checks the payment with
    the gateway server-to-server, then confirms the booking and issues tickets (idempotent)."""
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return error(400, "validation_failed", "Malformed request body.")
    provider = body.get("provider")

    try:
        if provider == "esewa":
            if body.get("failed") and not body.get("data"):
                return _fail_by_ref(body.get("ref"), "eSewa payment was cancelled or failed.")
            ref, paid_minor = gateways.esewa_check(str(body.get("data") or ""))
        elif provider == "khalti":
            ref = str(body.get("pidx") or "")
            if not ref:
                return error(400, "validation_failed", "Missing Khalti payment id.")
            status, paid_minor = gateways.khalti_check(ref)
            if status != "Completed":
                if status in ("User canceled", "Expired", "Refunded"):
                    return _fail_by_ref(ref, f"Khalti payment {status.lower()}.")
                return error(402, "payment_pending", f"Khalti reports the payment as {status}.")
        else:
            return error(400, "validation_failed", "Unknown payment provider.")
    except gateways.GatewayError as e:
        return error(402, "payment_not_verified", str(e))

    booking = Booking.objects.filter(payment_ref=ref).first()
    if not booking:
        return error(404, "not_found", "No booking matches that payment.")
    if booking.status == "confirmed":
        return JsonResponse(booking.to_dict())
    if booking.status != "pending_payment":
        return error(409, "booking_closed", "That booking expired before payment arrived. Contact support for a refund.")
    if paid_minor != booking.total_minor:
        return error(409, "amount_mismatch", "The paid amount does not match the booking total.")

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        if booking.status == "pending_payment":
            booking.status = "confirmed"
            booking.paid_at = timezone.now()
            booking.save(update_fields=["status", "paid_at"])
            _issue_tickets(booking)
    return JsonResponse(booking.to_dict())


def _fail_by_ref(ref, message):
    booking = Booking.objects.filter(payment_ref=ref).first() if ref else None
    if booking:
        _release(booking)
    return error(402, "payment_failed", message)


@require_http_methods(["GET"])
def my_bookings(request):
    """GET /v1/bookings/mine — the signed-in traveller's bookings, upcoming first."""
    user = optional_user(request)
    if not user:
        return error(401, "unauthorised", "Sign in to see your bookings.")
    _release_stale_pending()  # expire abandoned gateway payments so they don't linger as "pending"
    now = timezone.now()
    rows = list(
        Booking.objects.filter(user=user, status__in=["confirmed", "pending_payment"])
        .select_related("occurrence")
        .prefetch_related("passengers", "tickets")[:50]
    )
    rows.sort(key=lambda b: (b.departs_at is None or b.departs_at < now, b.departs_at or now))
    out = []
    for b in rows:
        d = b.to_dict()
        d["upcoming"] = bool(b.departs_at and b.departs_at >= now)
        out.append(d)
    return JsonResponse({"bookings": out})

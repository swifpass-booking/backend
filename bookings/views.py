import json
import re

from django.db import IntegrityError, transaction
from django.db.models import F
from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.jwt import decode_token
from accounts.models import User
from accounts.throttle import throttle
from accounts.views import is_valid_email, is_valid_phone
from catalog.models import PLATFORM_FEE_RATE, Occurrence

from .models import Booking, Passenger, Ticket

PAYMENT_METHODS = {"esewa", "khalti", "fonepay", "card"}
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
            return JsonResponse(existing.to_dict(), status=200)

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
            )
            for i, name in enumerate(names):
                passenger = Passenger.objects.create(booking=booking, full_name=name.strip(), is_lead=(i == 0), order=i)
                Ticket.objects.create(booking=booking, passenger=passenger, occurrence=occurrence)

            Occurrence.objects.filter(pk=occurrence.pk).update(seats_sold=F("seats_sold") + n)
    except IntegrityError:
        # Concurrent retry with the same Idempotency-Key lost the race.
        existing = Booking.objects.filter(idempotency_key=idem).first() if idem else None
        if existing:
            return JsonResponse(existing.to_dict(), status=200)
        raise

    return JsonResponse(booking.to_dict(), status=201)

import json

from django.http import JsonResponse
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.jwt import decode_token
from accounts.models import User
from catalog.models import Occurrence

from .models import Booking, Passenger, Ticket

PAYMENT_METHODS = {"esewa", "khalti", "fonepay", "card"}


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
def create_booking(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return error(400, "validation_failed", "Malformed request body.")

    offer = body.get("offer") or {}
    names = body.get("names") or []
    phone = body.get("phone")
    payment = body.get("payment")
    total = body.get("total") or {}

    if not offer.get("offerId") or not offer.get("title"):
        return error(400, "validation_failed", "An offer is required.")
    if not names or not all(isinstance(n, str) and n.strip() for n in names):
        return error(400, "validation_failed", "Every traveller needs a name.")
    if payment not in PAYMENT_METHODS:
        return error(400, "validation_failed", "Choose a valid payment method.")
    if not isinstance(total.get("amount"), int):
        return error(400, "validation_failed", "A total amount is required.")

    occurrence = Occurrence.objects.filter(id=offer.get("occurrenceId")).first()
    subtotal = int(offer.get("price", {}).get("amount", 0)) * len(names)
    fees = int(offer.get("fees", {}).get("amount", 0)) * len(names)

    booking = Booking.objects.create(
        user=optional_user(request),
        occurrence=occurrence,
        offer_id=offer["offerId"],
        mode=offer.get("mode", ""),
        title=offer["title"],
        provider_name=(offer.get("provider") or {}).get("displayName", ""),
        departs_at=parse_datetime(offer["departsAt"]) if offer.get("departsAt") else None,
        contact_phone=phone or "",
        contact_email=body.get("email") or None,
        payment_method=payment,
        passenger_count=len(names),
        subtotal_minor=subtotal,
        fees_minor=fees,
        total_minor=int(total["amount"]),
        currency=total.get("currency", "NPR"),
        created_via="web",
    )
    for i, n in enumerate(names):
        passenger = Passenger.objects.create(booking=booking, full_name=n.strip(), is_lead=(i == 0), order=i)
        Ticket.objects.create(booking=booking, passenger=passenger, occurrence=occurrence)

    if occurrence:
        occurrence.seats_sold += len(names)
        occurrence.save(update_fields=["seats_sold"])

    return JsonResponse(booking.to_dict(), status=201)

import logging
import re
import secrets
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.jwt import decode_user
from accounts.models import User
from accounts.throttle import throttle
from accounts.views import is_valid_email, is_valid_phone
from catalog import seating
from catalog.models import PLATFORM_FEE_RATE, InventoryZone, Occurrence
from swiftpass.http import error, parse_body

from carts.models import Hold

from . import gateways
from .models import Booking, Passenger, Ticket

log = logging.getLogger(__name__)

# Demo card processor: only well-known test PANs are accepted, so a real card can never be charged or stored.
TEST_CARDS = {"4242424242424242": "visa", "5555555555554444": "mastercard"}
DECLINED_TEST_CARD = "4000000000000002"


def _card_problem(card):
    """Message when the demo card is unusable, else None. The card is never saved."""
    if not isinstance(card, dict):
        return "Enter your card details."
    number = re.sub(r"\s", "", str(card.get("number", "")))
    m = re.fullmatch(r"(\d{2})\s*/\s*(\d{2})", str(card.get("expiry", "")).strip())
    if not m or not 1 <= int(m[1]) <= 12 or (2000 + int(m[2]), int(m[1])) < (timezone.now().year, timezone.now().month):
        return "Card expiry is invalid or in the past."
    if not re.fullmatch(r"\d{3}", str(card.get("cvc", ""))):
        return "Enter the 3-digit CVC."
    if number == DECLINED_TEST_CARD:
        return "Your card was declined (test card 4000 0000 0000 0002)."
    if number not in TEST_CARDS:
        return "Demo mode accepts test cards only: 4242 4242 4242 4242 (Visa) or 5555 5555 5555 4444 (Mastercard)."
    return None


PAYMENT_METHODS = {"esewa", "khalti", "fonepay", "card"}
GATEWAY_METHODS = {"esewa", "khalti", "fonepay"}  # real redirect flow on web; other methods stay instant-confirm
MAX_PASSENGERS = 9


PASSPORT_NUMBER_RE = re.compile(r"^[A-Z0-9]{5,12}$")


def is_international(occurrence):
    service = occurrence.service
    origin, dest = service.origin_place, service.dest_place
    return occurrence.mode == "air" and bool(origin and dest and origin.country != dest.country)


def clean_passport(raw, departs_at):
    """Validate one traveller's passport dict. Returns (fields, None) or (None, message)."""
    if not isinstance(raw, dict):
        return None, "Passport details are required for international flights."
    number = str(raw.get("passportNumber", "")).strip().upper()
    nationality = str(raw.get("nationality", "")).strip().upper()
    sex = raw.get("sex")
    try:
        birth = date.fromisoformat(raw.get("birthDate", ""))
        expiry = date.fromisoformat(raw.get("expiryDate", ""))
    except (TypeError, ValueError):
        return None, "Passport dates must be YYYY-MM-DD."
    if not PASSPORT_NUMBER_RE.match(number):
        return None, "Enter a valid passport number."
    if not re.fullmatch(r"[A-Z]{3}", nationality):
        return None, "Nationality must be a 3-letter code."
    if sex not in {"M", "F", "X"}:
        return None, "Sex must be M, F or X."
    if birth >= timezone.localdate():
        return None, "Date of birth must be in the past."
    # Airlines commonly require 6 months of validity remaining on the day of travel.
    min_expiry = departs_at.date() + timedelta(days=183)
    if expiry < min_expiry:
        return None, "Passport must be valid for at least 6 months after travel."
    return (number, nationality, birth, expiry, sex), None


def optional_user(request):
    header = request.headers.get("Authorization", "")
    token = header[7:] if header.startswith("Bearer ") else None
    if not token:
        return None
    return decode_user(token)


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
        if booking.zone_id:
            InventoryZone.objects.filter(pk=booking.zone_id).update(seats_sold=F("seats_sold") - booking.passenger_count)


def _release_stale_pending(batch=20):
    """Oldest first, a few per call: a burst of abandoned payments must not slow one unlucky request."""
    cutoff = timezone.now() - timedelta(minutes=settings.PENDING_PAYMENT_MINUTES)
    stale = list(Booking.objects.filter(status="pending_payment", created_at__lt=cutoff).order_by("created_at")[:batch])
    for b in stale:
        _release(b)
    return len(stale)


def _start_gateway(booking):
    if booking.payment_method == "esewa":
        ref = f"{booking.reference}-{secrets.token_hex(3)}"
        info = gateways.esewa_start(booking, ref)
    elif booking.payment_method == "fonepay":
        ref = f"{booking.reference}-{secrets.token_hex(3)}"
        info = gateways.fonepay_start(booking, ref)
    else:
        info = gateways.khalti_start(booking)
        ref = info["pidx"]
    # First writer wins: a second request that started its own payment meanwhile must not replace the one the traveller was sent to.
    saved = Booking.objects.filter(Q(payment_ref__isnull=True) | Q(payment_ref=""), pk=booking.pk).update(
        payment_ref=ref, gateway_info=info
    )
    if not saved:
        booking.refresh_from_db()
        return booking.gateway_info
    return info


def _issue_tickets(booking):
    for passenger in booking.passengers.all():
        Ticket.objects.get_or_create(
            passenger=passenger, defaults={"booking": booking, "occurrence": booking.occurrence, "seat_label": passenger.seat_label}
        )


def _with_gateway(booking, status):
    body = booking.to_dict()
    try:
        body["gateway"] = _start_gateway(booking)
    except gateways.GatewayError as e:
        booking.refresh_from_db()
        if booking.gateway_info:  # a parallel request already started the payment: keep it
            return JsonResponse({**booking.to_dict(), "gateway": booking.gateway_info}, status=status)
        _release(booking)
        return error(502, "gateway_unavailable", str(e))
    return JsonResponse(body, status=status)


def _same_order(booking, occurrence_id, zone_id, count, total_minor, seats):
    """Whether a retried request asks for the booking it first created (same Idempotency-Key)."""
    labels = sorted(p.seat_label for p in booking.passengers.all() if p.seat_label)
    return (
        booking.occurrence_id == occurrence_id
        and booking.zone_id == (zone_id or None)
        and booking.passenger_count == count
        and booking.total_minor == total_minor
        and (not seats or labels == sorted(seats))
    )


def _replay(idem, order, restart=True):
    """The answer for a retried request (same Idempotency-Key), or None when the key is new."""
    existing = Booking.objects.filter(idempotency_key=idem).first() if idem else None
    if not existing:
        return None
    if existing.status == "cancelled":  # dead booking (gateway failed, payment cancelled or expired): free the key for a fresh try
        Booking.objects.filter(pk=existing.pk).update(idempotency_key=None)
        return None
    if not _same_order(existing, *order):
        return error(409, "idempotency_conflict", "This checkout changed since it was first submitted. Start it again.")
    if existing.status == "pending_payment":
        # A started payment keeps its ref (restarting would orphan it): send the traveller back to that same payment.
        if existing.gateway_info:
            return JsonResponse({**existing.to_dict(), "gateway": existing.gateway_info}, status=200)
        if restart and not existing.payment_ref:
            return _with_gateway(existing, 200)
        # Still starting in another request, or booked before the details were kept: a reply without a gateway would read as "paid".
        return error(409, "payment_in_progress", "Your payment was already started. Finish it at the gateway, or try again in a few minutes.")
    return JsonResponse(existing.to_dict(), status=200)


@csrf_exempt
@require_http_methods(["POST"])
@throttle("booking", limit=20, window=600)
def create_booking(request):
    body = parse_body(request)
    if body is None:
        return error(400, "validation_failed", "Malformed request body.")

    offer = body.get("offer")
    names = body.get("names")
    phone = body.get("phone")
    if isinstance(phone, str):
        phone = re.sub(r"[\s\-()]", "", phone)
    email = body.get("email") or None
    payment = body.get("payment")
    total = body.get("total")
    seats = body.get("seats")
    zone_id = body.get("zoneId")

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
    # The X-Client header only picks the demo path when the server allows it; it never skips payment on its own.
    demo_instant = settings.DEMO_INSTANT_PAYMENT and request.headers.get("X-Client") == "mobile"
    if payment == "card" and not settings.DEMO_INSTANT_PAYMENT:
        return error(400, "validation_failed", "Card payments are not available.")
    if payment == "card" and not demo_instant:
        problem = _card_problem(body.get("card"))
        if problem:
            return error(402, "payment_failed", problem)
    if seats is not None and (
        not isinstance(seats, list) or not all(isinstance(x, str) and 0 < len(x) <= 12 for x in seats)
    ):
        return error(400, "validation_failed", "seats must be a list of seat labels.")
    if zone_id is not None and (not isinstance(zone_id, str) or seats):
        return error(400, "validation_failed", "zoneId is a string and cannot be combined with seats.")
    if not isinstance(total, dict) or not isinstance(total.get("amount"), int):
        return error(400, "validation_failed", "A total amount is required.")

    user = optional_user(request)
    if not user and request.headers.get("Authorization", "").startswith("Bearer "):
        return error(401, "unauthorised", "Session expired — sign in again.")
    raw_key = request.headers.get("Idempotency-Key", "").strip()[:100]
    idem = f"{user.id if user else 'anon'}:{raw_key}" if raw_key else None
    order = (offer["occurrenceId"], zone_id, len(names), total["amount"], seats)
    _release_stale_pending()  # first, so a retry never gets a payment link for a booking that has already expired
    replay = _replay(idem, order)
    if replay:
        return replay

    via_gateway = payment in GATEWAY_METHODS and not demo_instant

    try:
        with transaction.atomic():
            # Price and availability come from the database, never from the client.
            occurrence = (
                Occurrence.objects.select_for_update()
                .select_related("service", "service__provider")
                .filter(id=offer["occurrenceId"], status="scheduled", departs_at__gt=timezone.now())
                .first()
            )
            if not occurrence:
                return error(400, "validation_failed", "That trip or event is no longer available.")

            n = len(names)
            passport_fields = [None] * n
            if is_international(occurrence):
                passports = body.get("passports")
                if not isinstance(passports, list) or len(passports) != n:
                    return error(400, "passport_required", "Passport details are required for every traveller on an international flight.")
                for i, raw in enumerate(passports):
                    passport_fields[i], problem = clean_passport(raw, occurrence.departs_at)
                    if problem:
                        return error(400, "passport_invalid", f"Traveller {i + 1}: {problem}")
            # Seats other travellers are holding right now are not available; the caller's own holds are.
            others = Hold.objects.filter(occurrence=occurrence, expires_at__gt=timezone.now())
            if user:
                others = others.exclude(user_id=user.id)
            if occurrence.seats_available() - sum(h.qty for h in others) < n:
                return error(409, "seat_unavailable", "Not enough seats left.")

            zone = None
            if zone_id:
                zone = InventoryZone.objects.select_for_update().filter(id=zone_id, occurrence=occurrence).first()
                if not zone:
                    return error(400, "validation_failed", "zoneId does not belong to this trip.")
                if zone.is_seated():
                    return error(400, "validation_failed", "That zone has numbered seats: choose seats instead.")
                zone_others = Hold.objects.filter(zone=zone, expires_at__gt=timezone.now())
                if user:
                    zone_others = zone_others.exclude(user_id=user.id)
                if zone.capacity - zone.seats_sold - sum(h.qty for h in zone_others) < n:
                    return error(409, "seat_unavailable", "Not enough places left in that zone.")

            sections = seating.build_sections(occurrence)
            chosen = list(seats or [])
            if zone:
                chosen = []
                unit_prices = [zone.price_minor] * n
            elif sections:
                if not chosen:
                    chosen = seating.pick_default(occurrence, n, ignore_holds_of=user.id if user else None) or []
                    if len(chosen) != n:
                        return error(409, "seat_unavailable", "Not enough seats left together.")
                if len(chosen) != n or len(set(chosen)) != n:
                    return error(400, "validation_failed", "Choose one distinct seat per traveller.")
                index = seating.seat_index(sections)
                taken = seating.taken_labels(occurrence, sections, ignore_holds_of=user.id if user else None)
                for label in chosen:
                    if label not in index:
                        return error(400, "validation_failed", f"Seat {label} does not exist on this trip.")
                    if label in taken:
                        return error(409, "seat_unavailable", f"Seat {label} was just taken. Pick another seat.")
                unit_prices = [index[label][0]["price"]["amount"] for label in chosen]
            else:
                chosen = []
                unit_prices = [occurrence.base_price_minor] * n
            fee_list = [round(p * PLATFORM_FEE_RATE) for p in unit_prices]
            subtotal, fees = sum(unit_prices), sum(fee_list)
            if total["amount"] != subtotal + fees:
                return error(409, "price_changed", "The price changed. Review the new total and try again.")

            booking = Booking.objects.create(
                user=user,
                occurrence=occurrence,
                zone=zone,
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
                seat = chosen[i] if chosen else None
                passenger = Passenger.objects.create(
                    booking=booking, full_name=name.strip(), is_lead=(i == 0), order=i, seat_label=seat
                )
                if passport_fields[i]:
                    passenger.set_passport(*passport_fields[i])
                    passenger.save()
                if not via_gateway:
                    Ticket.objects.create(booking=booking, passenger=passenger, occurrence=occurrence, seat_label=seat)

            Occurrence.objects.filter(pk=occurrence.pk).update(seats_sold=F("seats_sold") + n)
            if zone:
                InventoryZone.objects.filter(pk=zone.pk).update(seats_sold=F("seats_sold") + n)
            if user:  # the traveller is buying: their holds on this trip are used up
                Hold.objects.filter(occurrence=occurrence, user_id=user.id).delete()
    except IntegrityError:
        # Concurrent retry with the same Idempotency-Key lost the race.
        replay = _replay(idem, order, restart=False)
        if replay:
            return replay
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
    body = parse_body(request)
    if body is None:
        return error(400, "validation_failed", "Malformed request body.")
    provider = body.get("provider")

    try:
        if provider == "esewa":
            if body.get("failed") and not body.get("data"):
                return _fail_esewa(body.get("ref"))
            ref, paid_minor = gateways.esewa_check(str(body.get("data") or ""))
        elif provider == "fonepay":
            query = body.get("query")
            if not isinstance(query, dict):
                return error(400, "validation_failed", "Missing Fonepay response.")
            ref, paid_minor = gateways.fonepay_check(query)
            if paid_minor is None:
                return _fail_by_ref(ref, "Fonepay payment was cancelled or failed.")
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
    if paid_minor != booking.total_minor:
        return error(409, "amount_mismatch", "The paid amount does not match the booking total.")
    if booking.status == "cancelled":
        return _recover_late_payment(booking)
    if booking.status != "pending_payment":
        return error(409, "booking_closed", "That booking is closed. Contact support.")

    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        if booking.status == "pending_payment":
            booking.status = "confirmed"
            booking.paid_at = timezone.now()
            booking.save(update_fields=["status", "paid_at"])
            _issue_tickets(booking)
    return JsonResponse(booking.to_dict())


def _recover_late_payment(booking):
    """The gateway confirms a payment for a booking we already released (paid after the 15-minute window).
    Re-confirm it if its seats are still free; otherwise record the payment so support can refund it."""
    with transaction.atomic():
        booking = Booking.objects.select_for_update().get(pk=booking.pk)
        occurrence = Occurrence.objects.select_for_update().filter(pk=booking.occurrence_id).first()
        labels = [p.seat_label for p in booking.passengers.all() if p.seat_label]
        seat_clash = bool(labels) and Passenger.objects.filter(
            booking__occurrence=occurrence, booking__status__in=["pending_payment", "confirmed"], seat_label__in=labels
        ).exclude(booking=booking).exists()
        zone = InventoryZone.objects.select_for_update().filter(pk=booking.zone_id).first() if booking.zone_id else None
        zone_full = bool(zone) and zone.capacity - zone.seats_sold < booking.passenger_count
        if (
            booking.status == "cancelled"
            and not zone_full
            and occurrence
            and occurrence.status == "scheduled"
            and occurrence.seats_available() >= booking.passenger_count
            and not seat_clash
        ):
            booking.status, booking.paid_at = "confirmed", timezone.now()
            booking.save(update_fields=["status", "paid_at"])
            Occurrence.objects.filter(pk=occurrence.pk).update(seats_sold=F("seats_sold") + booking.passenger_count)
            if zone:
                InventoryZone.objects.filter(pk=zone.pk).update(seats_sold=F("seats_sold") + booking.passenger_count)
            _issue_tickets(booking)
            return JsonResponse(booking.to_dict())
        if booking.status == "cancelled" and not booking.paid_at:
            booking.paid_at = timezone.now()  # cancelled + paid_at set = paid after expiry, refund due
            booking.save(update_fields=["paid_at"])
    log.error("Payment received after booking %s expired and its seats are gone: refund %s %s", booking.reference, booking.total_minor, booking.currency)
    return error(
        409, "booking_closed",
        f"Your payment arrived after the seats were released. We recorded it under {booking.reference} and will refund it.",
    )


def _fail_esewa(ref):
    """The browser says the eSewa payment failed, but it is unsigned: release the seats only if eSewa agrees."""
    booking = Booking.objects.filter(payment_ref=ref, status="pending_payment").first() if isinstance(ref, str) else None
    if booking:
        try:
            if gateways.esewa_status(ref, gateways._rupees(booking.total_minor)) == "CANCELED":
                _release(booking)
        except gateways.GatewayError:
            pass  # eSewa unreachable: the stale sweep frees the seats
    return error(402, "payment_failed", "eSewa payment was cancelled or failed.")


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
        .select_related("occurrence", "zone")
        .prefetch_related("passengers", "tickets__passenger")[:50]
    )
    rows.sort(key=lambda b: (b.departs_at is None or b.departs_at < now, b.departs_at or now))
    out = []
    for b in rows:
        d = b.to_dict()
        d["upcoming"] = bool(b.departs_at and b.departs_at >= now)
        out.append(d)
    return JsonResponse({"bookings": out})

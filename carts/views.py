from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.throttle import throttle
from accounts.views import require_auth
from catalog.models import InventoryZone, Occurrence
from swiftpass.http import error, parse_body

from .models import CART_TTL_SECONDS, HOLD_TTL_SECONDS_MAX, Cart, CartItem, Hold


def _int(value, default, low, high):
    """value as an int within [low, high], or None if it is not one."""
    if value in (None, ""):
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        n = int(value)
    except ValueError:
        return None
    return n if low <= n <= high else None


@csrf_exempt
@require_http_methods(["POST"])
@require_auth
@throttle("hold", limit=20, window=600, key_func=lambda r: str(r.user_id))  # per traveller: the voice agent shares one IP
def create_hold(request):
    body = parse_body(request)
    if body is None:
        return error(400, "validation_failed", "Malformed request body.")

    occurrence = Occurrence.objects.filter(id=body.get("occurrenceId"), status="scheduled", departs_at__gt=timezone.now()).first()
    zone = InventoryZone.objects.filter(id=body.get("zoneId"), occurrence=occurrence).first() if occurrence else None
    if not occurrence or not zone:
        return error(400, "validation_failed", "occurrenceId and zoneId must refer to a real zone on an upcoming trip.")

    seat_labels = body.get("seatLabels") or []
    if (
        not isinstance(seat_labels, list)
        or len(seat_labels) > 50
        or len(set(seat_labels)) != len(seat_labels)
        or not all(isinstance(s, str) and 0 < len(s) <= 12 for s in seat_labels)
    ):
        return error(400, "validation_failed", "seatLabels must be up to 50 distinct seat labels.")

    qty = len(seat_labels) if seat_labels else _int(body.get("qty"), 1, 1, 50)
    ttl_seconds = _int(body.get("ttlSeconds"), HOLD_TTL_SECONDS_MAX, 1, HOLD_TTL_SECONDS_MAX)
    if qty is None or ttl_seconds is None:
        return error(400, "validation_failed", "qty and ttlSeconds must be whole numbers in range.")

    def reject(response):  # undo the old-hold cleanup below: a refused request must not cost the traveller their hold
        transaction.set_rollback(True)
        return response

    # Lock the trip so two simultaneous holds cannot both pass the capacity check below.
    with transaction.atomic():
        occurrence = Occurrence.objects.select_for_update().get(pk=occurrence.pk)
        zone = InventoryZone.objects.get(pk=zone.pk)  # re-read under the lock: a booking may have just sold from it
        # One live hold per traveller per trip (unless already in a cart); expired ones are dead weight.
        now = timezone.now()
        Hold.objects.filter(occurrence=occurrence, cart_item__isnull=True).filter(
            Q(user_id=request.user_id) | Q(expires_at__lte=now)
        ).delete()
        active = sum(h.qty for h in Hold.objects.filter(occurrence=occurrence, expires_at__gt=now))
        if qty > occurrence.seats_available() - active:
            return reject(error(409, "seat_unavailable", "Not enough seats left on this trip."))
        if seat_labels:
            from catalog import seating

            sections = seating.build_sections(occurrence)
            index = seating.seat_index(sections)
            taken = seating.taken_labels(occurrence, sections)
            for label in seat_labels:
                if label not in index or index[label][0]["zoneId"] != zone.id:
                    return reject(error(400, "validation_failed", f"Seat {label} is not in this zone."))
                if label in taken:
                    return reject(error(409, "seat_unavailable", f"Seat {label} is not available."))
        if qty > zone.seats_left():
            return reject(error(409, "seat_unavailable", "Not enough seats left in this zone."))

        hold = Hold.objects.create(
            user_id=request.user_id,
            occurrence=occurrence,
            zone=zone,
            seat_labels=seat_labels,
            qty=qty,
            expires_at=timezone.now() + timezone.timedelta(seconds=ttl_seconds),
        )
    return JsonResponse(hold.to_dict(), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@require_auth
def create_cart(request):
    cart = Cart.objects.create(user_id=request.user_id, expires_at=timezone.now() + timezone.timedelta(seconds=CART_TTL_SECONDS))
    return JsonResponse(cart.to_dict(), status=201)


@csrf_exempt
@require_http_methods(["POST"])
@require_auth
def add_cart_item(request, cart_id):
    cart = Cart.objects.filter(id=cart_id, user_id=request.user_id).first()
    if not cart:
        return error(404, "validation_failed", "No such cart.")

    body = parse_body(request)
    if body is None:
        return error(400, "validation_failed", "Malformed request body.")

    hold = Hold.objects.filter(id=body.get("holdId"), user_id=request.user_id).first()
    if not hold:
        return error(400, "validation_failed", "holdId must refer to a hold you created.")
    if hold.is_expired():
        return error(409, "hold_expired", "That hold has expired — create a new one.")
    if hasattr(hold, "cart_item"):
        return error(409, "idempotency_conflict", "That hold is already in a cart.")

    leg_index = _int(body.get("legIndex"), 0, 0, 20)
    if leg_index is None:
        return error(400, "validation_failed", "legIndex must be a small whole number.")

    CartItem.objects.create(
        cart=cart,
        leg_index=leg_index,
        hold=hold,
        occurrence=hold.occurrence,
        seat_labels=hold.seat_labels,
        qty=hold.qty,
        unit_price_minor=hold.zone.price_minor,
    )
    return JsonResponse(cart.to_dict(), status=201)


@require_http_methods(["GET"])
@require_auth
def get_cart(request, cart_id):
    cart = Cart.objects.filter(id=cart_id, user_id=request.user_id).first()
    if not cart:
        return error(404, "validation_failed", "No such cart.")
    return JsonResponse(cart.to_dict())

import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.views import require_auth
from catalog.models import InventoryZone, Occurrence

from .models import CART_TTL_SECONDS, HOLD_TTL_SECONDS_MAX, Cart, CartItem, Hold


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def parse_body(request):
    try:
        return json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return None


@csrf_exempt
@require_http_methods(["POST"])
@require_auth
def create_hold(request):
    body = parse_body(request)
    if body is None:
        return error(400, "validation_failed", "Malformed request body.")

    occurrence = Occurrence.objects.filter(id=body.get("occurrenceId")).first()
    zone = InventoryZone.objects.filter(id=body.get("zoneId"), occurrence=occurrence).first() if occurrence else None
    if not occurrence or not zone:
        return error(400, "validation_failed", "occurrenceId and zoneId must refer to a real zone.")

    seat_labels = body.get("seatLabels") or []
    if not isinstance(seat_labels, list) or not all(isinstance(s, str) for s in seat_labels):
        return error(400, "validation_failed", "seatLabels must be a list of strings.")

    qty = len(seat_labels) if seat_labels else int(body.get("qty") or 1)
    if qty < 1:
        return error(400, "validation_failed", "qty must be at least 1.")
    if qty > zone.capacity - zone.held_seats():
        return error(409, "seat_unavailable", "Not enough seats left in this zone.")

    ttl_seconds = min(int(body.get("ttlSeconds") or HOLD_TTL_SECONDS_MAX), HOLD_TTL_SECONDS_MAX)

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

    item = CartItem.objects.create(
        cart=cart,
        leg_index=int(body.get("legIndex") or 0),
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

import uuid

from django.db import models
from django.utils import timezone

from catalog.models import PLATFORM_FEE_RATE, InventoryZone, Occurrence

HOLD_TTL_SECONDS_MAX = 600  # capped server-side, per the contract note on CreateHoldRequest.ttlSeconds
CART_TTL_SECONDS = 1200


def new_hold_id():
    return f"hld_{uuid.uuid4().hex[:20]}"


def new_cart_id():
    return f"crt_{uuid.uuid4().hex[:20]}"


def new_cart_item_id():
    return f"cti_{uuid.uuid4().hex[:20]}"


class Hold(models.Model):
    """A short-lived claim on seats/GA capacity in one zone, created before a
    cart item exists. `create_hold` is the only agent tool allowed to mutate
    inventory ahead of an actual purchase."""

    id = models.CharField(primary_key=True, max_length=32, default=new_hold_id, editable=False)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="holds"
    )
    occurrence = models.ForeignKey(Occurrence, on_delete=models.CASCADE, related_name="holds")
    zone = models.ForeignKey(InventoryZone, on_delete=models.CASCADE, related_name="holds")
    seat_labels = models.JSONField(default=list, blank=True)
    qty = models.PositiveSmallIntegerField(default=1)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def is_expired(self):
        return timezone.now() >= self.expires_at

    def to_dict(self):
        remaining = (self.expires_at - timezone.now()).total_seconds()
        return {
            "holdId": self.id,
            "occurrenceId": self.occurrence_id,
            "seatLabels": self.seat_labels,
            "expiresAt": self.expires_at.isoformat(),
            "expiresInSeconds": max(int(remaining), 0),
        }

    def __str__(self):
        return self.id


class Cart(models.Model):
    id = models.CharField(primary_key=True, max_length=32, default=new_cart_id, editable=False)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="carts"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def to_dict(self):
        items = list(self.items.select_related("hold", "occurrence").all())
        subtotal = sum(item.subtotal_minor() for item in items)
        fees = sum(item.fees_minor() for item in items)
        return {
            "cartId": self.id,
            "items": [item.to_dict() for item in items],
            "subtotal": {"amount": subtotal, "currency": "NPR"},
            "fees": {"amount": fees, "currency": "NPR"},
            "total": {"amount": subtotal + fees, "currency": "NPR"},
            "expiresAt": self.expires_at.isoformat(),
        }

    def __str__(self):
        return self.id


class CartItem(models.Model):
    id = models.CharField(primary_key=True, max_length=32, default=new_cart_item_id, editable=False)
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    leg_index = models.PositiveSmallIntegerField(default=0)
    hold = models.OneToOneField(Hold, on_delete=models.CASCADE, related_name="cart_item")
    occurrence = models.ForeignKey(Occurrence, on_delete=models.CASCADE, related_name="cart_items")
    seat_labels = models.JSONField(default=list, blank=True)
    qty = models.PositiveSmallIntegerField(default=1)
    # Snapshot the zone's price_minor at add-to-cart time so a later price
    # change on the zone doesn't retroactively reprice items already in cart.
    unit_price_minor = models.IntegerField()

    def subtotal_minor(self):
        return self.unit_price_minor * self.qty

    def fees_minor(self):
        return round(self.subtotal_minor() * PLATFORM_FEE_RATE)

    def to_dict(self):
        return {
            "cartItemId": self.id,
            "legIndex": self.leg_index,
            "offer": self.occurrence.to_offer_dict(),
            "holdId": self.hold_id,
            "seatLabels": self.seat_labels,
            "qty": self.qty,
            "lineTotal": {"amount": self.subtotal_minor() + self.fees_minor(), "currency": "NPR"},
        }

    def __str__(self):
        return self.id

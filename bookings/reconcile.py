"""Find payments the gateway took but our browser-return never confirmed (the customer closed the tab after paying)."""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from . import gateways
from .models import Booking
from .views import _settle

log = logging.getLogger(__name__)

LOOKBACK = timedelta(hours=2)  # a gateway session lives about an hour, so a payment cannot land on an older booking
MAX_GATEWAY_ERRORS = 3  # a down gateway must not make every remaining booking wait for its timeout


def _paid_minor(booking):
    """What the gateway says was paid for this booking, or None when it is unpaid or the method cannot be checked."""
    ref = booking.payment_ref
    if booking.payment_method == "esewa":
        status = gateways.esewa_status(ref, gateways._rupees(booking.total_minor))
        return booking.total_minor if status == "COMPLETE" else None  # the query carries our total, so COMPLETE means that amount
    if booking.payment_method == "khalti" and not ref.startswith("demo-"):
        status, paid = gateways.khalti_check(ref)
        return paid if status == "Completed" else None
    return None  # Fonepay has no status call (see gateways.fonepay_check)


def reconcile(limit=50):
    """Returns how many bookings were settled by this run."""
    since = timezone.now() - LOOKBACK
    candidates = (
        Booking.objects.filter(payment_method__in=["esewa", "khalti"], payment_ref__isnull=False, paid_at__isnull=True)
        .filter(Q(status="pending_payment") | Q(status="cancelled", created_at__gte=since))
        .order_by("-created_at")[:limit]  # newest first: old abandoned checkouts must not crowd out a recent payment
    )
    settled = errors = 0
    for booking in candidates:
        try:
            paid = _paid_minor(booking)
        except gateways.GatewayError as e:
            log.warning("Could not check payment for %s: %s", booking.reference, e)
            errors += 1
            if errors >= MAX_GATEWAY_ERRORS:
                break
            continue
        if paid is not None:
            _settle(booking, paid)
            settled += 1
    return settled

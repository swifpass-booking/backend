from django.db.models import Count, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from accounts.views import require_admin
from bookings.models import Booking
from catalog.models import Occurrence

MODE_ORDER = ["air", "bus", "rail", "event"]


@require_http_methods(["GET"])
@require_admin
def stats(request):
    confirmed = Booking.objects.filter(status="confirmed")
    revenue = confirmed.aggregate(total=Sum("total_minor"))["total"] or 0

    by_mode = {row["mode"]: row["n"] for row in confirmed.values("mode").annotate(n=Count("id"))}
    bookings_by_mode = [{"mode": m, "count": by_mode.get(m, 0)} for m in MODE_ORDER]

    recent = confirmed.order_by("-created_at")[:6]

    return JsonResponse(
        {
            "totalUsers": User.objects.count(),
            "totalBookings": confirmed.count(),
            "totalRevenue": {"amount": revenue, "currency": "NPR"},
            "upcomingOccurrences": Occurrence.objects.filter(
                status="scheduled", departs_at__gte=timezone.now()
            ).count(),
            "bookingsByMode": bookings_by_mode,
            "recentBookings": [b.to_dict() for b in recent],
        }
    )


@require_http_methods(["GET"])
@require_admin
def users(request):
    rows = User.objects.order_by("-created_at")[:200]
    return JsonResponse(
        {
            "users": [
                {**u.to_public_dict(), "createdAt": u.created_at.isoformat(), "bookingCount": u.bookings.count()}
                for u in rows
            ]
        }
    )


@require_http_methods(["GET"])
@require_admin
def occurrences(request):
    rows = Occurrence.objects.select_related("service", "service__provider").order_by("departs_at")[:200]
    return JsonResponse(
        {
            "occurrences": [
                {
                    "id": o.id,
                    "title": o.service.title,
                    "mode": o.mode,
                    "providerName": o.service.provider.display_name,
                    "departsAt": o.departs_at.isoformat(),
                    "status": o.status,
                    "capacity": o.capacity,
                    "seatsSold": o.seats_sold,
                    "basePrice": {"amount": o.base_price_minor, "currency": "NPR"},
                }
                for o in rows
            ]
        }
    )


@require_http_methods(["GET"])
@require_admin
def bookings(request):
    rows = Booking.objects.order_by("-created_at")[:200]
    return JsonResponse({"bookings": [b.to_dict() for b in rows]})

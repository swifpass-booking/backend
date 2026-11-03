from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts import authz
from accounts.views import require_staff
from bookings.models import Ticket
from catalog.models import Occurrence
from swiftpass.http import error, parse_body

from .models import ScanEvent


def log_scan(request, *, code, ticket, occurrence_id, gate, accepted, reason, message, passenger_name, trip_title, zone_label=None):
    ScanEvent.objects.create(
        code=code,
        ticket=ticket,
        occurrence_id=occurrence_id or (ticket.occurrence_id if ticket else None),
        gate=gate,
        scanned_by=request.admin_user,
        accepted=accepted,
        reason=reason,
        message=message,
        passenger_name=passenger_name,
        trip_title=trip_title,
        zone_label=zone_label,
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_staff
def redeem(request):
    body = parse_body(request)
    if body is None:
        return error(400, "validation_failed", "Malformed request body.")

    code = (body.get("code") or "").strip()
    occurrence_id = body.get("occurrenceId") or None
    gate = body.get("gate") or "Gate A"

    if not code:
        return error(400, "validation_failed", "A code is required.")

    ticket = Ticket.objects.select_related("passenger", "booking", "booking__zone").filter(code=code).first()
    zone_label = ticket.booking.zone.label if ticket and ticket.booking.zone_id else None
    now = timezone.now()

    # Check before any response that names the passenger, so staff of one provider learn nothing about another's tickets.
    for target in {occurrence_id, ticket.occurrence_id if ticket else None}:  # a set: the usual case is one id, one OpenFGA call
        if target and not authz.can_scan(request.admin_user.id, target):
            return error(403, "forbidden", "You cannot scan tickets for this event.")

    if not ticket:
        log_scan(
            request, code=code, ticket=None, occurrence_id=occurrence_id, gate=gate, accepted=False,
            reason="unknown_code", message="This code was not issued by Swiftpass.", passenger_name=None, trip_title=None,
        )
        return JsonResponse(
            {"accepted": False, "reason": "unknown_code", "message": "This code was not issued by Swiftpass.", "decidedAt": now.isoformat()}
        )

    if occurrence_id and ticket.occurrence_id != occurrence_id:
        log_scan(
            request, code=code, ticket=ticket, occurrence_id=occurrence_id, gate=gate, accepted=False,
            reason="wrong_occurrence", message="This ticket belongs to a different event.",
            passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title, zone_label=zone_label,
        )
        return JsonResponse(
            {
                "accepted": False,
                "reason": "wrong_occurrence",
                "message": "This ticket belongs to a different event.",
                "ticketId": ticket.id,
                "passengerName": ticket.passenger.full_name,
                "zoneLabel": zone_label,
                "decidedAt": now.isoformat(),
            }
        )

    if ticket.status == "void":
        log_scan(
            request, code=code, ticket=ticket, occurrence_id=occurrence_id, gate=gate, accepted=False,
            reason="revoked", message="This ticket was cancelled or refunded.",
            passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title, zone_label=zone_label,
        )
        return JsonResponse(
            {
                "accepted": False,
                "reason": "revoked",
                "message": "This ticket was cancelled or refunded.",
                "ticketId": ticket.id,
                "passengerName": ticket.passenger.full_name,
                "zoneLabel": zone_label,
                "decidedAt": now.isoformat(),
            }
        )

    def already_redeemed():
        message = f"Already admitted at {ticket.redeemed_gate}, {ticket.redeemed_at.strftime('%H:%M')}."
        log_scan(
            request, code=code, ticket=ticket, occurrence_id=occurrence_id, gate=gate, accepted=False,
            reason="already_redeemed", message=message,
            passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title, zone_label=zone_label,
        )
        return JsonResponse(
            {
                "accepted": False,
                "reason": "already_redeemed",
                "message": message,
                "ticketId": ticket.id,
                "passengerName": ticket.passenger.full_name,
                "zoneLabel": zone_label,
                "seatLabel": ticket.seat_label,
                "priorGate": ticket.redeemed_gate,
                "priorAt": ticket.redeemed_at.isoformat(),
                "decidedAt": now.isoformat(),
            }
        )

    if ticket.status == "redeemed":
        return already_redeemed()

    # One atomic UPDATE decides the winner: two gates scanning the same code at once cannot both pass.
    claimed = Ticket.objects.filter(pk=ticket.pk, status="issued").update(
        status="redeemed", redeemed_at=now, redeemed_gate=gate
    )
    if not claimed:
        ticket.refresh_from_db()
        if ticket.status != "redeemed":  # voided between our read and the update
            return error(409, "conflict", "The ticket just changed. Scan it again.")
        return already_redeemed()
    ticket.status, ticket.redeemed_at, ticket.redeemed_gate = "redeemed", now, gate

    log_scan(
        request, code=code, ticket=ticket, occurrence_id=occurrence_id, gate=gate, accepted=True,
        reason=None, message=None, passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title,
        zone_label=zone_label,
    )

    return JsonResponse(
        {
            "accepted": True,
            "reason": None,
            "ticketId": ticket.id,
            "passengerName": ticket.passenger.full_name,
            "zoneLabel": zone_label,
            "seatLabel": ticket.seat_label,
            "tripTitle": ticket.booking.title,
            "decidedAt": now.isoformat(),
        }
    )


@require_http_methods(["GET"])
@require_staff
def occurrences(request):
    """Upcoming/recent events the operator can scan against (the admin list is admin-only)."""
    cutoff = timezone.now() - timezone.timedelta(days=2)
    rows = Occurrence.objects.select_related("service", "service__provider").filter(departs_at__gte=cutoff, service__provider_id__in=request.staff_provider_ids).order_by("departs_at")[:200]
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
                }
                for o in rows
            ]
        }
    )


@require_http_methods(["GET"])
@require_staff
def stats(request):
    occurrence_id = request.GET.get("occurrenceId") or None

    if occurrence_id and not authz.can_scan(request.admin_user.id, occurrence_id):
        return error(403, "forbidden", "You cannot view this event.")
    qs = Ticket.objects.filter(occurrence__service__provider_id__in=request.staff_provider_ids)
    if occurrence_id:
        qs = qs.filter(occurrence_id=occurrence_id)
    return JsonResponse(
        {
            "issued": qs.count(),
            "redeemed": qs.filter(status="redeemed").count(),
            "void": qs.filter(status="void").count(),
        }
    )


@require_http_methods(["GET"])
@require_staff
def history(request):
    occurrence_id = request.GET.get("occurrenceId") or None
    try:
        limit = max(1, min(int(request.GET.get("limit", 100)), 500))
    except ValueError:
        limit = 100

    if occurrence_id and not authz.can_scan(request.admin_user.id, occurrence_id):
        return error(403, "forbidden", "You cannot view this event.")
    qs = ScanEvent.objects.select_related("scanned_by").filter(occurrence__service__provider_id__in=request.staff_provider_ids)
    if occurrence_id:
        qs = qs.filter(occurrence_id=occurrence_id)

    return JsonResponse({"scans": [e.to_dict() for e in qs[:limit]]})

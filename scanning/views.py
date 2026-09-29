import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.views import require_admin
from bookings.models import Ticket

from .models import ScanEvent


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def log_scan(request, *, code, ticket, occurrence_id, gate, accepted, reason, message, passenger_name, trip_title):
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
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin
def redeem(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return error(400, "validation_failed", "Malformed request body.")

    code = (body.get("code") or "").strip()
    occurrence_id = body.get("occurrenceId") or None
    gate = body.get("gate") or "Gate A"

    if not code:
        return error(400, "validation_failed", "A code is required.")

    ticket = Ticket.objects.select_related("passenger", "booking").filter(code=code).first()
    now = timezone.now()

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
            passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title,
        )
        return JsonResponse(
            {
                "accepted": False,
                "reason": "wrong_occurrence",
                "message": "This ticket belongs to a different event.",
                "ticketId": ticket.id,
                "passengerName": ticket.passenger.full_name,
                "decidedAt": now.isoformat(),
            }
        )

    if ticket.status == "void":
        log_scan(
            request, code=code, ticket=ticket, occurrence_id=occurrence_id, gate=gate, accepted=False,
            reason="revoked", message="This ticket was cancelled or refunded.",
            passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title,
        )
        return JsonResponse(
            {
                "accepted": False,
                "reason": "revoked",
                "message": "This ticket was cancelled or refunded.",
                "ticketId": ticket.id,
                "passengerName": ticket.passenger.full_name,
                "decidedAt": now.isoformat(),
            }
        )

    if ticket.status == "redeemed":
        message = f"Already admitted at {ticket.redeemed_gate}, {ticket.redeemed_at.strftime('%H:%M')}."
        log_scan(
            request, code=code, ticket=ticket, occurrence_id=occurrence_id, gate=gate, accepted=False,
            reason="already_redeemed", message=message,
            passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title,
        )
        return JsonResponse(
            {
                "accepted": False,
                "reason": "already_redeemed",
                "message": message,
                "ticketId": ticket.id,
                "passengerName": ticket.passenger.full_name,
                "seatLabel": ticket.seat_label,
                "priorGate": ticket.redeemed_gate,
                "priorAt": ticket.redeemed_at.isoformat(),
                "decidedAt": now.isoformat(),
            }
        )

    ticket.status = "redeemed"
    ticket.redeemed_at = now
    ticket.redeemed_gate = gate
    ticket.save(update_fields=["status", "redeemed_at", "redeemed_gate"])

    log_scan(
        request, code=code, ticket=ticket, occurrence_id=occurrence_id, gate=gate, accepted=True,
        reason=None, message=None, passenger_name=ticket.passenger.full_name, trip_title=ticket.booking.title,
    )

    return JsonResponse(
        {
            "accepted": True,
            "reason": None,
            "ticketId": ticket.id,
            "passengerName": ticket.passenger.full_name,
            "seatLabel": ticket.seat_label,
            "tripTitle": ticket.booking.title,
            "decidedAt": now.isoformat(),
        }
    )


@require_http_methods(["GET"])
@require_admin
def stats(request):
    occurrence_id = request.GET.get("occurrenceId") or None

    qs = Ticket.objects.all() if occurrence_id is None else Ticket.objects.filter(occurrence_id=occurrence_id)
    return JsonResponse(
        {
            "issued": qs.count(),
            "redeemed": qs.filter(status="redeemed").count(),
            "void": qs.filter(status="void").count(),
        }
    )


@require_http_methods(["GET"])
@require_admin
def history(request):
    occurrence_id = request.GET.get("occurrenceId") or None
    try:
        limit = min(int(request.GET.get("limit", 100)), 500)
    except ValueError:
        limit = 100

    qs = ScanEvent.objects.select_related("scanned_by")
    if occurrence_id:
        qs = qs.filter(occurrence_id=occurrence_id)

    return JsonResponse({"scans": [e.to_dict() for e in qs[:limit]]})

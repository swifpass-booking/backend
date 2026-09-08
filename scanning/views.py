import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from accounts.views import require_admin
from bookings.models import Ticket


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
@require_admin
def redeem(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return error(400, "validation_failed", "Malformed request body.")

    code = (body.get("code") or "").strip()
    occurrence_id = body.get("occurrenceId")
    gate = body.get("gate") or "Gate A"

    if not code or not occurrence_id:
        return error(400, "validation_failed", "A code and occurrenceId are required.")

    ticket = Ticket.objects.select_related("passenger", "booking").filter(code=code).first()
    now = timezone.now()

    if not ticket:
        return JsonResponse(
            {"accepted": False, "reason": "unknown_code", "message": "This code was not issued by Swiftpass.", "decidedAt": now.isoformat()}
        )

    if ticket.occurrence_id != occurrence_id:
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
        return JsonResponse(
            {
                "accepted": False,
                "reason": "already_redeemed",
                "message": f"Already admitted at {ticket.redeemed_gate}, {ticket.redeemed_at.strftime('%H:%M')}.",
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
    occurrence_id = request.GET.get("occurrenceId")
    if not occurrence_id:
        return error(400, "validation_failed", "occurrenceId is required.")

    qs = Ticket.objects.filter(occurrence_id=occurrence_id)
    return JsonResponse(
        {
            "issued": qs.count(),
            "redeemed": qs.filter(status="redeemed").count(),
            "void": qs.filter(status="void").count(),
        }
    )

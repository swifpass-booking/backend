import json
import uuid

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import InventoryZone, Occurrence


def error(status, code, message):
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


@csrf_exempt
@require_http_methods(["POST"])
def search(request):
    """POST /v1/search — the only 'safe' read tool the agent uses to find offers.

    Only single-leg search is implemented (no cross-provider bundling): each
    leg in the request is queried independently and its matches are merged
    into one offers[] list, same as a one-leg trip would be.
    """
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return error(400, "validation_failed", "Malformed request body.")

    legs = body.get("legs") or []
    if not legs:
        return error(400, "validation_failed", "At least one leg is required.")

    modes = body.get("modes")
    max_price = body.get("maxPrice")

    offers = []
    partial_failures = []
    for leg in legs:
        date_str = leg.get("date")
        the_date = parse_date(date_str) if date_str else None
        if not the_date:
            partial_failures.append({"providerId": "search", "reason": f"invalid leg date {date_str!r}"})
            continue

        qs = Occurrence.objects.select_related(
            "service", "service__provider", "service__origin_place", "service__dest_place", "service__venue_place"
        ).filter(status="scheduled", departs_at__date=the_date)

        if modes:
            qs = qs.filter(mode__in=modes)

        from_term = (leg.get("from") or "").strip().lower()
        to_term = (leg.get("to") or "").strip().lower()

        for occurrence in qs:
            service = occurrence.service
            if from_term and not _place_matches(service.origin_place, from_term) and not _place_matches(
                service.venue_place, from_term
            ):
                continue
            if to_term and not _place_matches(service.dest_place, to_term) and not _place_matches(
                service.venue_place, to_term
            ):
                continue
            if max_price is not None and occurrence.base_price_minor > max_price:
                continue
            offers.append(occurrence.to_offer_dict())

    return JsonResponse(
        {
            "requestId": f"req_{uuid.uuid4().hex[:16]}",
            "offers": offers,
            "bundles": [],
            "nextCursor": None,
            "partialFailures": partial_failures,
        }
    )


def _place_matches(place, term):
    if not place:
        return False
    return term in place.city.lower() or term in place.name.lower() or (place.code or "").lower() == term


@require_http_methods(["GET"])
def seatmap(request, occurrence_id):
    occurrence = Occurrence.objects.filter(id=occurrence_id).first()
    if not occurrence:
        return error(404, "validation_failed", "No such occurrence.")

    zones = InventoryZone.objects.filter(occurrence=occurrence)
    has_reserved = any(z.is_reserved_seating for z in zones)

    seats = []
    if has_reserved:
        for zone in zones:
            if not zone.is_reserved_seating:
                continue
            held = zone.held_seats()
            # Individual seat identities aren't tracked pre-cart — only how many
            # of this zone's seats are claimed by active holds — so seats are
            # synthesized labels split between 'held' and 'available'.
            for i in range(zone.capacity):
                seats.append(
                    {
                        "label": f"{zone.code}-{i + 1}",
                        "zoneId": zone.id,
                        "state": "held" if i < held else "available",
                        "price": {"amount": zone.price_minor, "currency": "NPR"},
                        "attributes": [],
                    }
                )

    return JsonResponse(
        {
            "occurrenceId": occurrence.id,
            "isReservedSeating": has_reserved,
            "layout": {"rows": 0, "columns": [], "aisleAfter": []},
            "seats": seats,
            "snapshotAt": timezone.now().isoformat(),
        }
    )

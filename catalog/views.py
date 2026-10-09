import json
import uuid

from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import geo, routing

from .models import InventoryZone, Occurrence, VehiclePosition


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
        if date_str and not the_date:
            partial_failures.append({"providerId": "search", "reason": f"invalid leg date {date_str!r}"})
            continue

        qs = Occurrence.objects.select_related(
            "service", "service__provider", "service__origin_place", "service__dest_place", "service__venue_place"
        ).filter(status="scheduled")
        # No date given: everything that hasn't departed yet.
        qs = qs.filter(departs_at__date=the_date) if the_date else qs.filter(departs_at__gte=timezone.now())

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
    from . import seating

    occurrence = (
        Occurrence.objects.select_related("service").filter(id=occurrence_id).first()
    )
    if not occurrence:
        return error(404, "validation_failed", "No such occurrence.")

    sections, seats = seating.seatmap(occurrence)
    return JsonResponse(
        {
            "occurrenceId": occurrence.id,
            "mode": occurrence.mode,
            "isReservedSeating": bool(sections),
            "layout": {"rows": sum(len(s["rows"]) for s in sections), "columns": [], "aisleAfter": []},
            "sections": sections,
            "seats": seats,
            "snapshotAt": timezone.now().isoformat(),
        }
    )


@require_http_methods(["GET"])
def places(request):
    """GET /v1/places?mode=bus — distinct cities that have scheduled trips/events for a mode
    (powers the search form's From/To/City pickers)."""
    mode = request.GET.get("mode")
    qs = Occurrence.objects.filter(status="scheduled").select_related(
        "service__origin_place", "service__dest_place", "service__venue_place"
    )
    if mode:
        qs = qs.filter(mode=mode)
    cities = set()
    for occ in qs:
        for place in (occ.service.origin_place, occ.service.dest_place, occ.service.venue_place):
            if place:
                cities.add(place.city)
    return JsonResponse({"mode": mode, "cities": sorted(cities)})


@require_http_methods(["GET"])
def featured(request):
    """GET /v1/featured — up to 4 upcoming offers, one per mode first (home page highlights)."""
    upcoming = (
        Occurrence.objects.filter(status="scheduled", departs_at__gte=timezone.now())
        .select_related("service", "service__provider", "service__origin_place", "service__dest_place", "service__venue_place")
        .order_by("departs_at")
    )
    picked, seen = [], set()
    for occ in upcoming:
        if occ.mode not in seen:
            seen.add(occ.mode)
            picked.append(occ)
    for occ in upcoming:
        if len(picked) >= 4:
            break
        if occ not in picked:
            picked.append(occ)
    return JsonResponse({"offers": [o.to_offer_dict() for o in picked[:4]]})


def _place_point(place):
    if not place or place.latitude is None or place.longitude is None:
        return None
    return {"name": place.name, "city": place.city, "lat": place.latitude, "lng": place.longitude}


@csrf_exempt
@require_http_methods(["GET", "POST"])
def tracking(request, occurrence_id):
    """GET  /v1/occurrences/<id>/tracking — where is this bus/train/flight right now?
    POST /v1/occurrences/<id>/tracking — operator staff push a GPS fix {lat, lng, speedKmh?, heading?}.

    With a fix newer than 10 minutes the position is `source: "gps"`. Otherwise it is estimated from
    the timetable along the straight line between origin and destination and marked `"simulated"` —
    the UI shows that label so nobody mistakes an estimate for a real GPS reading.
    """
    occ = (
        Occurrence.objects.select_related("service", "service__origin_place", "service__dest_place")
        .filter(id=occurrence_id)
        .first()
    )
    if not occ:
        return error(404, "not_found", "No such trip.")

    if request.method == "POST":
        from accounts.jwt import decode_token
        from accounts.models import User

        header = request.headers.get("Authorization", "")
        user_id = decode_token(header[7:]) if header.startswith("Bearer ") else None
        user = User.objects.filter(id=user_id).first() if user_id else None
        if not user or user.role not in ("operator_staff", "admin"):
            return error(403, "forbidden", "Only operator staff can report vehicle positions.")
        try:
            body = json.loads(request.body or "{}")
            lat, lng = float(body["lat"]), float(body["lng"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return error(400, "validation_failed", "lat and lng are required numbers.")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return error(400, "validation_failed", "Coordinates out of range.")
        VehiclePosition.objects.update_or_create(
            occurrence=occ,
            defaults={
                "latitude": lat,
                "longitude": lng,
                "speed_kmh": body.get("speedKmh"),
                "heading": body.get("heading"),
                "updated_at": timezone.now(),
            },
        )

    origin, dest = _place_point(occ.service.origin_place), _place_point(occ.service.dest_place)
    if not origin or not dest:
        return error(422, "not_trackable", "This trip has no route to show on a map (events are not tracked).")

    now = timezone.now()
    o_pt, d_pt = (origin["lat"], origin["lng"]), (dest["lat"], dest["lng"])
    road = routing.road_route(o_pt, d_pt) if occ.mode == "bus" else None  # only buses follow roads
    route = road["points"] if road else [list(o_pt), list(d_pt)]
    total_km = road["distance_km"] if road else geo.haversine_km(*o_pt, *d_pt)

    fix = VehiclePosition.objects.filter(occurrence=occ).first()
    if fix and now - fix.updated_at < timedelta(minutes=10):
        progress = geo.snap_fraction(route, fix.latitude, fix.longitude) if road else max(
            0.0, min(1.0, 1 - geo.haversine_km(fix.latitude, fix.longitude, *d_pt) / total_km if total_km else 1.0)
        )
        pos = {
            "lat": fix.latitude,
            "lng": fix.longitude,
            "speedKmh": fix.speed_kmh,
            "heading": fix.heading,
            "updatedAt": fix.updated_at.isoformat(),
            "source": "gps",
        }
        remaining_km = total_km * (1 - progress)
        speed = fix.speed_kmh or 0
        eta = round(remaining_km / speed * 60) if speed > 5 else None
        status = "arrived" if remaining_km < 1 else "en_route"
    else:
        if occ.arrives_at and occ.departs_at and occ.arrives_at > occ.departs_at:
            span = (occ.arrives_at - occ.departs_at).total_seconds()
            progress = max(0.0, min(1.0, (now - occ.departs_at).total_seconds() / span))
        else:
            progress = 1.0 if now >= occ.departs_at else 0.0
        status = "not_started" if now < occ.departs_at else ("arrived" if progress >= 1 else "en_route")
        lat, lng = geo.point_at(route, progress)
        ahead = geo.point_at(route, min(1.0, progress + 0.01))
        behind = geo.point_at(route, max(0.0, progress - 0.01))
        pos = {
            "lat": lat,
            "lng": lng,
            "speedKmh": None,
            "heading": geo.bearing(*behind, *ahead) if behind != ahead else geo.bearing(*o_pt, *d_pt),
            "updatedAt": now.isoformat(),
            "source": "simulated",
        }
        eta = round((occ.arrives_at - now).total_seconds() / 60) if occ.arrives_at and now < occ.arrives_at else None

    return JsonResponse(
        {
            "occurrenceId": occ.id,
            "mode": occ.mode,
            "title": occ.service.title,
            "departsAt": occ.departs_at.isoformat(),
            "arrivesAt": occ.arrives_at.isoformat() if occ.arrives_at else None,
            "origin": origin,
            "destination": dest,
            "position": pos,
            "progress": round(progress, 4),
            "status": status,
            "etaMinutes": eta,
            "totalKm": round(total_km, 1),
            "route": route,
            "routeSource": "road" if road else "straight",
        }
    )

"""Seat layouts and availability.

A layout is derived from the vehicle/venue (aircraft type, coach type, train class, cinema vs. open
event) and each zone's capacity, so every zone gets exactly `capacity` seats. Layouts are not stored;
what *is* stored is who holds which seat (Passenger.seat_label, Hold.seat_labels).

Seats that were sold before individual seats were tracked (Occurrence.seats_sold minus seats we know
by label) are shown as taken at deterministic pseudo-random positions, so the map looks like a real
vehicle but never changes between refreshes.
"""
import random
import string

from django.utils import timezone

from .models import PLATFORM_FEE_RATE

# Zones this large are standing/general admission: no seat picking (a stadium stand is not a seat map).
MAX_SEATED_EVENT_ZONE = 500
PLATFORM_FEE = PLATFORM_FEE_RATE


def zone_is_seated(occurrence, zone):
    """Whether travellers pick individual seats in this zone."""
    if occurrence.mode == "event":
        return zone.capacity <= MAX_SEATED_EVENT_ZONE
    return True


def _row_letters(n):
    """A..Z, AA.. for row n (0-based)."""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = string.ascii_uppercase[r] + s
    return s


def _columns(occurrence, zone):
    """Seat column pattern for a zone; None marks the aisle."""
    attrs = occurrence.service.attributes or {}
    mode = occurrence.mode
    if mode == "air":
        aircraft = (attrs.get("aircraft") or "").lower()
        if zone.code == "BIZ":
            return ["A", "C", None, "D", "F"]
        if any(k in aircraft for k in ("dornier", "twin otter")):
            return ["A", "B"]
        if any(k in aircraft for k in ("atr", "crj")):
            return ["A", "B", None, "C", "D"]
        return ["A", "B", "C", None, "D", "E", "F"]
    if mode == "bus":
        coach = attrs.get("coachType", "deluxe")
        if coach in ("luxury", "sleeper"):
            return ["A", None, "B", "C"]
        return ["A", "B", None, "C", "D"]
    if mode == "rail":
        return ["A", "B", "C", None, "D", "E"]
    return None


def _seat_types(columns):
    seats = [c for c in columns if c]
    types = {}
    for i, c in enumerate(seats):
        idx = columns.index(c)
        left_aisle = idx + 1 < len(columns) and columns[idx + 1] is None
        right_aisle = idx > 0 and columns[idx - 1] is None
        if i in (0, len(seats) - 1):
            t = ["window"]
        else:
            t = []
        if left_aisle or right_aisle:
            t.append("aisle")
        types[c] = t
    return types


def zone_rows(occurrence, zone, start_row=0):
    """Rows for one seated zone: [{"row": "12", "seats": [{"label", "col"} | None]}] with None for aisles."""
    rows = []
    if occurrence.mode == "event":
        cinema = (occurrence.service.attributes or {}).get("category") == "cinema"
        per_row = 12 if cinema else 20
        aisle_after = per_row // 2
        remaining = zone.capacity
        r = start_row
        while remaining > 0:
            n = min(per_row, remaining)
            letter = _row_letters(r)
            seats = []
            for i in range(n):
                if i == aisle_after:
                    seats.append(None)
                label = f"{letter}{i + 1}" if cinema else f"{zone.code}-{letter}{i + 1}"
                seats.append({"label": label, "col": str(i + 1), "attributes": []})
            rows.append({"row": letter, "seats": seats})
            remaining -= n
            r += 1
        return rows

    columns = _columns(occurrence, zone)
    per_row = len([c for c in columns if c])
    types = _seat_types(columns)
    remaining = zone.capacity
    r = start_row
    while remaining > 0:
        number = str(r + 1)
        seats, placed = [], 0
        for c in columns:
            if c is None:
                seats.append(None)
                continue
            if placed >= remaining:
                break
            seats.append({"label": f"{number}{c}", "col": c, "attributes": list(types[c])})
            placed += 1
        # drop a trailing aisle left by a short last row
        while seats and seats[-1] is None:
            seats.pop()
        rows.append({"row": number, "seats": seats})
        remaining -= placed
        r += 1
    return rows


def build_sections(occurrence, zones=None):
    """Seated zones with their rows. Cinemas list the cheapest zone first (front rows); vehicles and
    open events list the premium zone first (cabin front, stage side)."""
    zones = list(zones if zones is not None else occurrence.zones.all())
    seated = [z for z in zones if z.is_reserved_seating and zone_is_seated(occurrence, z)]
    cinema = occurrence.mode == "event" and (occurrence.service.attributes or {}).get("category") == "cinema"
    seated.sort(key=lambda z: z.price_minor, reverse=not cinema)

    sections, next_row = [], 0
    for z in seated:
        rows = zone_rows(occurrence, z, start_row=next_row)
        next_row += len(rows)
        fee = round(z.price_minor * PLATFORM_FEE)
        sections.append(
            {
                "zoneId": z.id,
                "code": z.code,
                "label": z.label,
                "price": {"amount": z.price_minor, "currency": "NPR"},
                "fee": {"amount": fee, "currency": "NPR"},
                "rows": rows,
            }
        )
    return sections


def labels_of(sections):
    return [s["label"] for sec in sections for row in sec["rows"] for s in row["seats"] if s]


def taken_labels(occurrence, sections):
    """{label: state} where state is 'sold' (booked or sold before seat tracking) or 'held'."""
    from bookings.models import Passenger
    from carts.models import Hold

    real = set(
        Passenger.objects.filter(
            booking__occurrence=occurrence, booking__status__in=["pending_payment", "confirmed"]
        )
        .exclude(seat_label__isnull=True)
        .values_list("seat_label", flat=True)
    )
    all_labels = labels_of(sections)
    state = {label: "sold" for label in real}

    # Sold before individual seats were tracked: scatter deterministically over the map.
    legacy = max(occurrence.seats_sold - len(real), 0)
    if legacy and all_labels and occurrence.capacity:
        count = min(round(legacy * len(all_labels) / occurrence.capacity), len(all_labels))
        shuffled = all_labels[:]
        random.Random(occurrence.id).shuffle(shuffled)
        for label in shuffled[:count]:
            state.setdefault(label, "sold")

    now = timezone.now()
    for hold in Hold.objects.filter(occurrence=occurrence, expires_at__gt=now):
        for label in hold.seat_labels or []:
            state.setdefault(label, "held")
    return state


def seat_index(sections):
    """label -> (section, seat dict)."""
    out = {}
    for sec in sections:
        for row in sec["rows"]:
            for s in row["seats"]:
                if s:
                    out[s["label"]] = (sec, s)
    return out


def seatmap(occurrence):
    sections = build_sections(occurrence)
    taken = taken_labels(occurrence, sections)
    flat = []
    for sec in sections:
        for row in sec["rows"]:
            for s in row["seats"]:
                if not s:
                    continue
                s["state"] = taken.get(s["label"], "available")
                s["price"] = sec["price"]
                s["fee"] = sec["fee"]
                s["zoneId"] = sec["zoneId"]
                flat.append(s)
    return sections, flat


def pick_default(occurrence, n):
    """First n free seats in the base-price zone (what an un-seated booking would have cost)."""
    sections = build_sections(occurrence)
    taken = taken_labels(occurrence, sections)
    ordered = sorted(sections, key=lambda sec: abs(sec["price"]["amount"] - occurrence.base_price_minor))
    for sec in ordered:
        free = [
            s["label"] for row in sec["rows"] for s in row["seats"] if s and s["label"] not in taken
        ]
        if len(free) >= n:
            return free[:n]
    return None

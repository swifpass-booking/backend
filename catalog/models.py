from django.db import models

# Platform service fee, applied on top of price_minor when quoting an offer or
# cart line. There's no separate fee schedule per provider yet, so one flat
# rate stands in for it everywhere fees are computed (search, carts).
PLATFORM_FEE_RATE = 0.05


class Place(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    kind = models.CharField(max_length=20)
    code = models.CharField(max_length=10, null=True, blank=True)
    name = models.CharField(max_length=255)
    name_ne = models.CharField(max_length=255, null=True, blank=True)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=5)
    timezone = models.CharField(max_length=50)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def to_dict(self):
        return {
            "id": self.id,
            "kind": self.kind,
            "code": self.code,
            "name": self.name,
            "nameNe": self.name_ne,
            "city": self.city,
            "country": self.country,
            "timezone": self.timezone,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }

    def __str__(self):
        return self.name


class Provider(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    legal_name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    modes = models.JSONField(default=list)
    adapter_key = models.CharField(max_length=50)
    is_self_serve = models.BooleanField(default=False)

    def to_dict(self):
        return {"id": self.id, "displayName": self.display_name, "isSelfServe": self.is_self_serve}

    def __str__(self):
        return self.display_name


class Service(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="services")
    mode = models.CharField(max_length=10)
    code = models.CharField(max_length=30, null=True, blank=True)
    title = models.CharField(max_length=255)
    origin_place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dest_place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    venue_place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    attributes = models.JSONField(default=dict)

    def __str__(self):
        return self.title


class Occurrence(models.Model):
    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("cancelled", "Cancelled"),
        ("completed", "Completed"),
    ]

    id = models.CharField(primary_key=True, max_length=32)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="occurrences")
    mode = models.CharField(max_length=10)
    departs_at = models.DateTimeField()
    arrives_at = models.DateTimeField(null=True, blank=True)
    gates_open_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="scheduled")
    base_price_minor = models.IntegerField()
    capacity = models.IntegerField()
    seats_sold = models.IntegerField(default=0)
    provider_ref = models.CharField(max_length=100, null=True, blank=True)

    def seats_available(self):
        return max(self.capacity - self.seats_sold, 0)

    def to_offer_dict(self, offer_id=None, quote_ttl_seconds=900):
        from django.utils import timezone

        service = self.service
        zones = list(self.zones.all())
        fees_minor = round(self.base_price_minor * PLATFORM_FEE_RATE)

        details = dict(service.attributes or {})
        if self.mode == "air":
            details.setdefault("flightNumber", service.code or "")
            details.setdefault("aircraft", None)
            details.setdefault("cabin", "economy")
            details.setdefault("baggageKg", 20)
            details.setdefault("stops", 0)
        elif self.mode == "bus":
            details.setdefault("coachType", "deluxe")
            details.setdefault("boardingPoint", service.origin_place.name if service.origin_place else "")
            details.setdefault("droppingPoint", service.dest_place.name if service.dest_place else "")
            details.setdefault("amenities", [])
            details.setdefault("hasAc", True)
        elif self.mode == "rail":
            details.setdefault("trainNumber", service.code or "")
            details.setdefault("coachClass", "general")
            details.setdefault("platform", None)
        elif self.mode == "event":
            details.setdefault("category", "community")
            details.setdefault("ageRating", None)
            details.setdefault("language", None)
            details.setdefault("doorsOpenAt", self.gates_open_at.isoformat() if self.gates_open_at else None)

        return {
            "offerId": offer_id or f"ofr_{self.id}",
            "occurrenceId": self.id,
            "mode": self.mode,
            "provider": service.provider.to_dict(),
            "title": service.title,
            "origin": service.origin_place.to_dict() if service.origin_place else None,
            "destination": service.dest_place.to_dict() if service.dest_place else None,
            "venue": service.venue_place.to_dict() if service.venue_place else None,
            "departsAt": self.departs_at.isoformat(),
            "arrivesAt": self.arrives_at.isoformat() if self.arrives_at else None,
            "durationMinutes": (
                int((self.arrives_at - self.departs_at).total_seconds() // 60) if self.arrives_at else None
            ),
            "price": {"amount": self.base_price_minor, "currency": "NPR"},
            "fees": {"amount": fees_minor, "currency": "NPR"},
            "seatsAvailable": self.seats_available(),
            "hasReservedSeating": any(z.is_seated() for z in zones),
            "zones": [z.to_dict() for z in zones],
            "rankingReason": [],
            "details": details,
            "quoteExpiresAt": (timezone.now() + timezone.timedelta(seconds=quote_ttl_seconds)).isoformat(),
        }

    def __str__(self):
        return f"{self.service.title} @ {self.departs_at.isoformat()}"


class InventoryZone(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    occurrence = models.ForeignKey(Occurrence, on_delete=models.CASCADE, related_name="zones")
    code = models.CharField(max_length=20)
    label = models.CharField(max_length=100)
    price_minor = models.IntegerField()
    capacity = models.IntegerField()
    is_reserved_seating = models.BooleanField(default=False)

    def is_seated(self):
        """Reserved seating flag AND small enough that travellers can pick a seat (see catalog.seating)."""
        from .seating import zone_is_seated

        return self.is_reserved_seating and zone_is_seated(self.occurrence, self)

    def held_seats(self):
        # Active (non-expired) holds against this zone. Imported lazily to
        # avoid a circular import — carts.models imports catalog.models.
        from django.utils import timezone

        from carts.models import Hold

        return sum(
            h.qty for h in Hold.objects.filter(zone=self, expires_at__gt=timezone.now())
        )

    def to_dict(self):
        available = max(self.capacity - self.held_seats(), 0)
        return {
            "id": self.id,
            "code": self.code,
            "label": self.label,
            "price": {"amount": self.price_minor, "currency": "NPR"},
            "seatsAvailable": available,
            "isReservedSeating": self.is_seated(),
        }

    def __str__(self):
        return f"{self.occurrence_id}:{self.code}"


class VehiclePosition(models.Model):
    """Latest reported GPS fix for the vehicle running one occurrence. Pushed by the operator's
    tracker/driver app via POST /v1/occurrences/<id>/tracking. With no fix on file, the tracking
    endpoint estimates the position from the timetable and labels it "simulated"."""

    occurrence = models.OneToOneField(Occurrence, on_delete=models.CASCADE, related_name="vehicle_position")
    latitude = models.FloatField()
    longitude = models.FloatField()
    speed_kmh = models.FloatField(null=True, blank=True)
    heading = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField()

    def __str__(self):
        return f"{self.occurrence_id} @ {self.latitude:.4f},{self.longitude:.4f}"


class RouteCache(models.Model):
    """Road geometry between two places, fetched once from OSRM and reused for every trip on that route."""

    key = models.CharField(primary_key=True, max_length=80)  # "lat,lng>lat,lng", 4 dp
    geometry = models.JSONField()  # [[lat, lng], ...], evenly spaced by distance
    distance_km = models.FloatField()
    duration_min = models.FloatField()
    fetched_at = models.DateTimeField(auto_now_add=True)

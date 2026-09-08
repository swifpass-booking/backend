import json

from django.conf import settings
from django.core.management.base import CommandError
from django.core.management.base import BaseCommand

from catalog.models import InventoryZone, Occurrence, Place, Provider, Service

MOCK_DATA_PATH = settings.BASE_DIR.parent / "frontend" / "src" / "mock-data.json"


class Command(BaseCommand):
    help = "Load places/providers/services/occurrences/zones from the frontend's mock-data.json into the database."

    def handle(self, *args, **options):
        if not MOCK_DATA_PATH.exists():
            raise CommandError(f"Could not find {MOCK_DATA_PATH}")

        data = json.loads(MOCK_DATA_PATH.read_text(encoding="utf-8"))

        for p in data["places"]:
            Place.objects.update_or_create(
                id=p["id"],
                defaults=dict(
                    kind=p["kind"],
                    code=p.get("code"),
                    name=p["name"],
                    name_ne=p.get("nameNe"),
                    city=p["city"],
                    country=p["country"],
                    timezone=p["timezone"],
                ),
            )

        for pr in data["providers"]:
            Provider.objects.update_or_create(
                id=pr["id"],
                defaults=dict(
                    legal_name=pr["legalName"],
                    display_name=pr["displayName"],
                    modes=pr["modes"],
                    adapter_key=pr["adapterKey"],
                    is_self_serve=pr["isSelfServe"],
                ),
            )

        for s in data["services"]:
            Service.objects.update_or_create(
                id=s["id"],
                defaults=dict(
                    provider_id=s["providerId"],
                    mode=s["mode"],
                    code=s.get("code"),
                    title=s["title"],
                    origin_place_id=s.get("originPlaceId"),
                    dest_place_id=s.get("destPlaceId"),
                    venue_place_id=s.get("venuePlaceId"),
                    attributes=s.get("attributes", {}),
                ),
            )

        for o in data["occurrences"]:
            Occurrence.objects.update_or_create(
                id=o["id"],
                defaults=dict(
                    service_id=o["serviceId"],
                    mode=o["mode"],
                    departs_at=o["departsAt"],
                    arrives_at=o.get("arrivesAt"),
                    gates_open_at=o.get("gatesOpenAt"),
                    status=o["status"],
                    base_price_minor=o["basePriceMinor"],
                    capacity=o["capacity"],
                    seats_sold=o["seatsSold"],
                    provider_ref=o.get("providerRef"),
                ),
            )

        for z in data["inventoryZones"]:
            InventoryZone.objects.update_or_create(
                id=z["id"],
                defaults=dict(
                    occurrence_id=z["occurrenceId"],
                    code=z["code"],
                    label=z["label"],
                    price_minor=z["priceMinor"],
                    capacity=z["capacity"],
                    is_reserved_seating=z["isReservedSeating"],
                ),
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {Place.objects.count()} places, {Provider.objects.count()} providers, "
                f"{Service.objects.count()} services, {Occurrence.objects.count()} occurrences, "
                f"{InventoryZone.objects.count()} zones."
            )
        )

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.geo import CITY_COORDS
from catalog.models import InventoryZone, Occurrence, Place, Provider, Service

# (place id, city, name, name_ne)
PLACES = [
    ("plc_hotel_ktm", "Kathmandu", "Thamel Heritage Hotel", None),
    ("plc_hotel_pkr", "Pokhara", "Lakeside Boutique Hotel", None),
    ("plc_resort_pkr", "Pokhara", "Fewa Shore Resort", None),
    ("plc_resort_chitwan", "Chitwan", "Jungle Safari Resort", None),
    ("plc_hall_ktm", "Kathmandu", "Grand Banquet Hall, Baneshwor", None),
    ("plc_hall_pkr", "Pokhara", "Lakeview Conference Hall", None),
]

# (provider id, name, mode, service id, place id, title, attributes, [(zone code, label, price NPR, capacity)])
STAYS = [
    ("prv_thamel_heritage", "Thamel Heritage Hotel", "hotel", "svc_thamel_heritage", "plc_hotel_ktm",
     "Thamel Heritage Hotel — per night",
     {"starRating": 4, "amenities": ["Wi-Fi", "Breakfast", "Airport pickup"]},
     [("STD", "Standard room", 4500, 12), ("DLX", "Deluxe room", 6800, 8), ("STE", "Suite", 12000, 3)]),
    ("prv_lakeside_boutique", "Lakeside Boutique", "hotel", "svc_lakeside_boutique", "plc_hotel_pkr",
     "Lakeside Boutique Hotel — per night",
     {"starRating": 3, "amenities": ["Wi-Fi", "Lake view", "Rooftop cafe"]},
     [("STD", "Standard room", 3500, 10), ("LKV", "Lake-view room", 5200, 6)]),
    ("prv_fewa_shore", "Fewa Shore Resort", "resort", "svc_fewa_shore", "plc_resort_pkr",
     "Fewa Shore Resort — per night",
     {"starRating": 5, "amenities": ["Pool", "Spa", "Breakfast", "Boat rides"]},
     [("CTG", "Garden cottage", 9500, 8), ("VLA", "Lake villa", 18000, 4)]),
    ("prv_jungle_safari", "Jungle Safari Resort", "resort", "svc_jungle_safari", "plc_resort_chitwan",
     "Jungle Safari Resort — per night (all meals)",
     {"starRating": 4, "amenities": ["Jungle safari", "All meals", "Pool"]},
     [("TNT", "Safari tent", 7000, 10), ("CTG", "Cottage", 10500, 6)]),
    ("prv_grand_banquet", "Grand Banquet Hall", "hall", "svc_grand_banquet", "plc_hall_ktm",
     "Grand Banquet Hall — full-day booking",
     {"capacityGuests": 500, "amenities": ["Stage", "Catering", "Parking", "AC"], "eventTypes": ["Wedding", "Reception", "Corporate"]},
     [("MAIN", "Main hall (up to 500 guests)", 150000, 1), ("MINI", "Mini hall (up to 150 guests)", 60000, 1)]),
    ("prv_lakeview_conf", "Lakeview Conference Hall", "hall", "svc_lakeview_conf", "plc_hall_pkr",
     "Lakeview Conference Hall — full-day booking",
     {"capacityGuests": 200, "amenities": ["Projector", "Wi-Fi", "Catering"], "eventTypes": ["Conference", "Workshop", "Seminar"]},
     [("CONF", "Conference room (up to 200)", 45000, 1)]),
]

DAYS_AHEAD = 30


class Command(BaseCommand):
    help = "Seed hotels, resorts and event halls: one bookable occurrence per property per day for the next 30 days."

    def handle(self, *args, **options):
        for pid, city, name, name_ne in PLACES:
            lat, lng = CITY_COORDS.get(city, (None, None))
            Place.objects.update_or_create(
                id=pid,
                defaults=dict(kind="venue", name=name, name_ne=name_ne, city=city, country="NP",
                              timezone="Asia/Kathmandu", latitude=lat, longitude=lng),
            )

        today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        for prov_id, prov_name, mode, svc_id, place_id, title, attrs, zones in STAYS:
            Provider.objects.update_or_create(
                id=prov_id,
                defaults=dict(legal_name=prov_name, display_name=prov_name, modes=[mode],
                              adapter_key="native_console", is_self_serve=False),
            )
            Service.objects.update_or_create(
                id=svc_id,
                defaults=dict(provider_id=prov_id, mode=mode, title=title, venue_place_id=place_id, attributes=attrs),
            )
            for day in range(DAYS_AHEAD):
                start = today + timedelta(days=day, hours=14 if mode != "hall" else 9)
                end = today + timedelta(days=day + 1, hours=11 if mode != "hall" else 0)
                occ_id = f"occ_{svc_id[4:]}_{day:02d}"
                occ, _ = Occurrence.objects.update_or_create(
                    id=occ_id,
                    defaults=dict(
                        service_id=svc_id, mode=mode, departs_at=start,
                        arrives_at=end if mode != "hall" else start.replace(hour=23),
                        gates_open_at=start, status="scheduled",
                        base_price_minor=min(z[2] for z in zones) * 100,
                        capacity=sum(z[3] for z in zones),
                    ),
                )
                for code, label, price, cap in zones:
                    InventoryZone.objects.update_or_create(
                        id=f"zon_{occ_id[4:]}_{code.lower()}",
                        defaults=dict(occurrence=occ, code=code, label=label, price_minor=price * 100,
                                      capacity=cap, is_reserved_seating=False),
                    )
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(STAYS)} stay/hall properties x {DAYS_AHEAD} days."))

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from catalog.models import InventoryZone, Occurrence


class Command(BaseCommand):
    help = "Create a Kathmandu→Pokhara bus that is mid-journey right now, so live tracking has something to show."

    def add_arguments(self, parser):
        parser.add_argument("--service", default="svc_ktm_pkr")
        parser.add_argument("--started-hours-ago", type=float, default=2.0)
        parser.add_argument("--duration-hours", type=float, default=6.0)

    def handle(self, *args, **opts):
        template = Occurrence.objects.filter(service_id=opts["service"]).order_by("departs_at").first()
        if not template:
            self.stderr.write(f"No occurrence found for service {opts['service']}; run seed_catalog first.")
            return
        now = timezone.now()
        departs = now - timedelta(hours=opts["started_hours_ago"])
        occ, created = Occurrence.objects.update_or_create(
            id="occ_live_bus",
            defaults=dict(
                service=template.service,
                mode=template.mode,
                departs_at=departs,
                arrives_at=departs + timedelta(hours=opts["duration_hours"]),
                status="scheduled",
                base_price_minor=template.base_price_minor,
                capacity=template.capacity,
                seats_sold=0,
            ),
        )
        for z in InventoryZone.objects.filter(occurrence=template):
            InventoryZone.objects.update_or_create(
                id=f"{z.id}_live",
                defaults=dict(occurrence=occ, code=z.code, label=z.label, price_minor=z.price_minor, capacity=z.capacity, is_reserved_seating=z.is_reserved_seating),
            )
        self.stdout.write(f"{'Created' if created else 'Updated'} {occ.id}: departed {departs:%H:%M} UTC, arrives {occ.arrives_at:%H:%M} UTC")

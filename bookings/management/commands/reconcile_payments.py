from django.core.management.base import BaseCommand

from bookings.reconcile import reconcile


class Command(BaseCommand):
    help = (
        "Ask eSewa and Khalti about unconfirmed bookings and confirm (or record as refund due) the ones that were "
        "paid but never came back to the site. Safe to run often; start.sh runs it at boot."
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS(f"Settled {reconcile(options['limit'])} paid booking(s)."))

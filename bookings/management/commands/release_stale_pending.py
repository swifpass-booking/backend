from django.core.management.base import BaseCommand

from bookings.views import _release_stale_pending


class Command(BaseCommand):
    help = "Give back the seats of gateway bookings that were never paid. Safe to run from a cron job."

    def handle(self, *args, **options):
        _release_stale_pending(batch=500)

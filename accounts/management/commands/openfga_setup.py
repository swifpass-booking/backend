from django.core.management.base import BaseCommand, CommandError

from accounts import authz


class Command(BaseCommand):
    help = "Create the OpenFGA store if it is missing and write the current authorization model. Safe to re-run."

    def handle(self, *args, **options):
        try:
            store_id = authz.ensure_store_and_model()
        except authz.AuthzError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"OpenFGA store ready: {store_id}"))

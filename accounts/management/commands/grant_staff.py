from django.core.management.base import BaseCommand, CommandError

from accounts import authz
from accounts.models import User
from catalog.models import Provider


class Command(BaseCommand):
    help = "Make an existing account operator staff or a gate agent for one provider."

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email or phone (E.164) of the account.")
        parser.add_argument("--provider", required=True, help="Provider id.")
        parser.add_argument("--role", default="gate_agent", choices=authz.STAFF_RELATIONS)

    def handle(self, *args, **options):
        identifier = options["identifier"].strip()
        user = User.objects.filter(email=identifier).first() or User.objects.filter(phone_e164=identifier).first()
        if not user:
            raise CommandError(f"No account for {identifier}.")
        if not Provider.objects.filter(id=options["provider"]).exists():
            raise CommandError(f"No provider {options['provider']}.")
        try:
            authz.grant_staff(user, options["provider"], options["role"])
        except authz.AuthzError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"{identifier} is now {options['role']} for {options['provider']}."))

from django.core.management.base import BaseCommand, CommandError

from accounts import authz
from accounts.models import User


class Command(BaseCommand):
    help = "Remove platform-admin and/or provider-staff rights from an account (the opposite of create_admin / grant_staff)."

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email or phone (E.164) of the account.")
        parser.add_argument("--admin", action="store_true", help="Remove platform-admin rights.")
        parser.add_argument("--provider", help="Remove staff rights on this provider id only.")
        parser.add_argument("--all-staff", action="store_true", help="Remove staff rights on every provider.")

    def handle(self, *args, **options):
        if not (options["admin"] or options["provider"] or options["all_staff"]):
            raise CommandError("Say what to remove: --admin, --provider ID or --all-staff.")
        identifier = options["identifier"].strip()
        user = User.objects.filter(email__iexact=identifier).first() or User.objects.filter(phone_e164=identifier).first()
        if not user:
            raise CommandError(f"No account for {identifier}.")
        try:
            if options["admin"]:
                authz.revoke_admin(user)
            if options["provider"] or options["all_staff"]:
                authz.revoke_staff(user, options["provider"])
        except authz.AuthzError as e:
            raise CommandError(str(e))
        user.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f"Rights removed for {identifier}; role label is now {user.role}."))

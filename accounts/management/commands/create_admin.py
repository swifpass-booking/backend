from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Create a new admin user, or promote an existing account (matched by email/phone) to admin."

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email or phone (E.164) to log in with.")
        parser.add_argument("--password", required=False, help="Required when creating a new account.")
        parser.add_argument("--full-name", default="Admin", help="Used only when creating a new account.")

    def handle(self, *args, **options):
        identifier = options["identifier"].strip()
        is_email = "@" in identifier

        user = User.objects.filter(email=identifier).first() or User.objects.filter(phone_e164=identifier).first()
        if user:
            user.role = "admin"
            user.save(update_fields=["role"])
            self.stdout.write(self.style.SUCCESS(f"Promoted existing user {identifier} to admin."))
            return

        if not options["password"]:
            raise CommandError("--password is required when creating a new admin account.")

        user = User(
            email=identifier if is_email else None,
            phone_e164=identifier if not is_email else None,
            full_name=options["full_name"],
            role="admin",
        )
        user.set_password(options["password"])
        user.save()
        self.stdout.write(self.style.SUCCESS(f"Created admin user {identifier}."))

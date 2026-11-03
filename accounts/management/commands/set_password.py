from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import F

from accounts.models import User


class Command(BaseCommand):
    help = "Support tool: set a new password for an account that cannot sign in. Signs the account out everywhere."

    def add_arguments(self, parser):
        parser.add_argument("--identifier", required=True, help="Email or phone (E.164) of the account.")
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        identifier = options["identifier"].strip()
        user = User.objects.filter(email__iexact=identifier).first() or User.objects.filter(phone_e164=identifier).first()
        if not user:
            raise CommandError(f"No account for {identifier}.")
        try:
            validate_password(options["password"], user=user)
        except ValidationError as e:
            raise CommandError(" ".join(e.messages))
        user.set_password(options["password"])
        user.token_version = F("token_version") + 1  # every token issued before this stops working
        user.save(update_fields=["password_hash", "token_version"])
        self.stdout.write(self.style.SUCCESS(f"Password changed for {identifier}."))

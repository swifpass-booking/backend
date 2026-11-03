from django.core.management.base import BaseCommand, CommandError

from accounts import authz
from accounts.models import User
from catalog.models import Provider


class Command(BaseCommand):
    help = (
        "ONE-TIME, run by hand (start.sh must not run it: it would re-grant every provider to every staff member on each deploy). "
        "Copy the legacy User.role column into OpenFGA tuples. Admins become platform admins. Staff had no "
        "provider before, so they are granted every existing provider; narrow them with grant_staff afterwards."
    )

    def handle(self, *args, **options):
        providers = list(Provider.objects.values_list("id", flat=True))
        try:
            for user in User.objects.filter(role="admin"):
                authz.grant_admin(user)
            for user in User.objects.filter(role__in=authz.STAFF_RELATIONS):
                for provider_id in providers:
                    authz.grant_staff(user, provider_id, user.role)
        except authz.AuthzError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS("OpenFGA tuples synced from User.role."))

"""Role checks against a real OpenFGA server. Skipped unless OPENFGA_TEST_URL points at one
(e.g. `openfga run` -> http://localhost:8080)."""
import json
import os
import urllib.request
import uuid

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts import authz
from accounts.jwt import issue_token
from accounts.models import User
from bookings.models import Booking, Passenger, Ticket
from catalog.models import Occurrence, Provider, Service

URL = os.environ.get("OPENFGA_TEST_URL", "")


def _reachable():
    try:
        urllib.request.urlopen(URL.rstrip("/") + "/healthz", timeout=2)
        return True
    except Exception:
        return False


@override_settings(OPENFGA_API_URL=URL, OPENFGA_API_TOKEN="", OPENFGA_STORE_ID="")
class OpenFGARoleTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not URL or not _reachable():
            from unittest import SkipTest

            raise SkipTest("set OPENFGA_TEST_URL to a running OpenFGA to run these tests")

    def setUp(self):
        cache.clear()
        authz._store_id = None
        self._override = override_settings(OPENFGA_STORE_NAME=f"test-{uuid.uuid4().hex[:8]}")
        self._override.enable()
        authz.ensure_store_and_model()

        self.prov = {}
        self.occ = {}
        for key in ("a", "b"):
            provider = Provider.objects.create(id=f"p{key}", legal_name=key, display_name=key, adapter_key="x")
            service = Service.objects.create(id=f"s{key}", provider=provider, mode="bus", title=f"Bus {key}")
            self.prov[key] = provider
            self.occ[key] = Occurrence.objects.create(
                id=f"occ_{key}", service=service, mode="bus", departs_at=timezone.now() + timezone.timedelta(days=1),
                base_price_minor=100000, capacity=5,
            )
        mk = lambda n: User.objects.create(email=f"{n}@x.co", full_name=n, password_hash="x")
        self.admin, self.staff_a, self.traveller = mk("admin"), mk("staffa"), mk("trav")
        authz.grant_admin(self.admin)
        authz.grant_staff(self.staff_a, "pa", "gate_agent")

    def tearDown(self):
        self._override.disable()
        authz._store_id = None

    def _get(self, user, path):
        return self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {issue_token(user)}")

    def _post(self, user, path, body):
        return self.client.post(path, json.dumps(body), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {issue_token(user)}")

    def test_admin_endpoints_need_the_admin_tuple(self):
        self.assertEqual(self._get(self.admin, "/v1/admin/stats").status_code, 200)
        self.assertEqual(self._get(self.staff_a, "/v1/admin/stats").status_code, 403)
        self.assertEqual(self._get(self.traveller, "/v1/admin/stats").status_code, 403)

    def test_db_role_label_alone_grants_nothing(self):
        User.objects.filter(id=self.traveller.id).update(role="admin")
        self.assertEqual(self._get(self.traveller, "/v1/admin/stats").status_code, 403)
        self.assertEqual(self._get(self.traveller, "/v1/scan/occurrences").status_code, 403)

    def test_staff_only_see_their_providers_occurrences(self):
        ids = [o["id"] for o in self._get(self.staff_a, "/v1/scan/occurrences").json()["occurrences"]]
        self.assertEqual(ids, ["occ_a"])
        ids = {o["id"] for o in self._get(self.admin, "/v1/scan/occurrences").json()["occurrences"]}
        self.assertEqual(ids, {"occ_a", "occ_b"})

    def test_staff_cannot_read_or_scan_another_providers_event(self):
        self.assertEqual(self._get(self.staff_a, "/v1/scan/stats?occurrenceId=occ_a").status_code, 200)
        self.assertEqual(self._get(self.staff_a, "/v1/scan/stats?occurrenceId=occ_b").status_code, 403)
        self.assertEqual(self._get(self.staff_a, "/v1/scan/history?occurrenceId=occ_b").status_code, 403)

        booking = Booking.objects.create(
            occurrence=self.occ["b"], offer_id="o", mode="bus", title="Bus b", provider_name="b",
            departs_at=self.occ["b"].departs_at, contact_phone="9800000000", payment_method="esewa", passenger_count=1,
            subtotal_minor=1, fees_minor=0, total_minor=1, currency="NPR", status="confirmed",
        )
        passenger = Passenger.objects.create(booking=booking, full_name="Rita")
        ticket = Ticket.objects.create(booking=booking, passenger=passenger, occurrence=self.occ["b"])
        res = self._post(self.staff_a, "/v1/scan/redeem", {"code": ticket.code, "occurrenceId": "occ_a"})
        self.assertEqual(res.status_code, 403)
        self.assertNotIn("Rita", res.content.decode())
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "issued")
        self.assertEqual(self._post(self.admin, "/v1/scan/redeem", {"code": ticket.code, "occurrenceId": "occ_b"}).status_code, 200)

    def test_only_staff_of_the_provider_can_report_position(self):
        body = {"lat": 27.7, "lng": 85.3}
        self.assertEqual(self._post(self.traveller, "/v1/occurrences/occ_a/tracking", body).status_code, 403)
        self.assertEqual(self._post(self.staff_a, "/v1/occurrences/occ_b/tracking", body).status_code, 403)
        self.assertNotEqual(self._post(self.staff_a, "/v1/occurrences/occ_a/tracking", body).status_code, 403)

    def test_checks_fail_closed_when_openfga_is_down(self):
        with override_settings(OPENFGA_API_URL="http://127.0.0.1:9"), self.assertLogs("accounts.authz", "ERROR"):
            self.assertEqual(self._get(self.admin, "/v1/admin/stats").status_code, 403)
            self.assertEqual(self._get(self.staff_a, "/v1/scan/occurrences").status_code, 403)

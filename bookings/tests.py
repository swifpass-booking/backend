import json

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from catalog.models import InventoryZone, Occurrence, Provider, Service

from .models import Booking


class SecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        provider = Provider.objects.create(id="p1", legal_name="P", display_name="P", adapter_key="x")
        service = Service.objects.create(id="s1", provider=provider, mode="bus", title="KTM-PKR")
        self.occ = Occurrence.objects.create(
            id="occ_1", service=service, mode="bus", departs_at=timezone.now() + timezone.timedelta(days=1),
            base_price_minor=100000, capacity=2,
        )

    def _book(self, n=1, total=None, key=None, occ="occ_1"):
        total = 105000 * n if total is None else total
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        return self.client.post(
            "/v1/bookings",
            data=json.dumps({
                "offer": {"occurrenceId": occ, "price": {"amount": 1}},
                "names": ["Asha Rai"] * n, "phone": "9800000000", "email": "a@b.co",
                "payment": "esewa", "total": {"amount": total, "currency": "NPR"},
            }),
            content_type="application/json", **headers,
        )

    def test_client_price_is_ignored_and_mismatch_rejected(self):
        self.assertEqual(self._book(total=1).status_code, 409)
        self.assertEqual(Booking.objects.count(), 0)

    def test_server_price_used(self):
        res = self._book()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(Booking.objects.get().total_minor, 105000)

    def test_unknown_occurrence_rejected(self):
        self.assertEqual(self._book(occ="occ_nope").status_code, 400)

    def test_no_overselling(self):
        self.assertEqual(self._book(n=2).status_code, 201)
        self.assertEqual(self._book(n=1).status_code, 409)
        self.occ.refresh_from_db()
        self.assertEqual(self.occ.seats_sold, 2)

    def test_idempotent_retry(self):
        a = self._book(key="k1")
        b = self._book(key="k1")
        self.assertEqual(a.json()["reference"], b.json()["reference"])
        self.assertEqual(Booking.objects.count(), 1)
        self.occ.refresh_from_db()
        self.assertEqual(self.occ.seats_sold, 1)


class SeatSelectionTests(TestCase):
    def setUp(self):
        cache.clear()
        provider = Provider.objects.create(id="p1", legal_name="P", display_name="P", adapter_key="x")
        service = Service.objects.create(
            id="s1", provider=provider, mode="bus", title="KTM-PKR", attributes={"coachType": "deluxe"}
        )
        self.occ = Occurrence.objects.create(
            id="occ_1", service=service, mode="bus", departs_at=timezone.now() + timezone.timedelta(days=1),
            base_price_minor=100000, capacity=8,
        )
        InventoryZone.objects.create(
            id="z1", occurrence=self.occ, code="DELUXE", label="Deluxe", price_minor=100000, capacity=8,
            is_reserved_seating=True,
        )

    def _book(self, seats, n=None, total=None):
        n = n or len(seats or [1])
        body = {
            "offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"] * n, "phone": "9800000000",
            "payment": "card", "total": {"amount": 105000 * n if total is None else total, "currency": "NPR"},
        }
        if seats is not None:
            body["seats"] = seats
        return self.client.post("/v1/bookings", data=json.dumps(body), content_type="application/json")

    def test_seatmap_has_capacity_seats(self):
        data = self.client.get("/v1/occurrences/occ_1/seatmap").json()
        self.assertTrue(data["isReservedSeating"])
        self.assertEqual(len(data["seats"]), 8)
        self.assertEqual(data["seats"][0]["label"], "1A")

    def test_chosen_seat_is_stored_and_blocks_others(self):
        res = self._book(["2C"])
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["tickets"][0]["seatLabel"], "2C")
        self.assertEqual(self._book(["2C"]).status_code, 409)
        seats = {s["label"]: s["state"] for s in self.client.get("/v1/occurrences/occ_1/seatmap").json()["seats"]}
        self.assertEqual(seats["2C"], "sold")

    def test_unknown_or_duplicate_seat_rejected(self):
        self.assertEqual(self._book(["9Z"]).status_code, 400)
        self.assertEqual(self._book(["1A", "1A"], n=2).status_code, 400)
        self.assertEqual(self._book(["1A"], n=2).status_code, 400)

    def test_no_seats_given_auto_assigns(self):
        res = self._book(None, n=2)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(sorted(t["seatLabel"] for t in res.json()["tickets"]), ["1A", "1B"])


class AuthTests(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, path, body, **kw):
        return self.client.post(path, data=json.dumps(body), content_type="application/json", **kw)

    def test_weak_password_rejected(self):
        res = self._post("/v1/auth/register", {"fullName": "Asha Rai", "identifier": "a@b.co", "password": "12345678"})
        self.assertEqual(res.status_code, 400)

    def test_logout_revokes_token(self):
        res = self._post("/v1/auth/register", {"fullName": "Asha Rai", "identifier": "a@b.co", "password": "t3st-Pass-9x!"})
        self.assertEqual(res.status_code, 201)
        auth = {"HTTP_AUTHORIZATION": f"Bearer {res.json()['token']}"}
        self.assertEqual(self.client.get("/v1/auth/me", **auth).status_code, 200)
        self.assertEqual(self._post("/v1/auth/logout", {}, **auth).status_code, 200)
        self.assertEqual(self.client.get("/v1/auth/me", **auth).status_code, 401)

    def test_login_lockout(self):
        User.objects.create(email="a@b.co", full_name="A B")
        codes = [self._post("/v1/auth/login", {"identifier": "a@b.co", "password": "wrong"}).status_code for _ in range(7)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)

import json

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import User
from catalog.models import InventoryZone, Occurrence, Place, Provider, Service

from .models import Booking, Ticket
from .views import refund_booking


class SecurityTests(TestCase):
    def setUp(self):
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
        b = self._book(key="k1")  # payment already started: same booking, same payment
        self.assertEqual((a.status_code, b.status_code), (201, 200))
        self.assertEqual(Booking.objects.count(), 1)
        self.occ.refresh_from_db()
        self.assertEqual(self.occ.seats_sold, 1)


@override_settings(DEMO_INSTANT_PAYMENT=True)
class SeatSelectionTests(TestCase):
    def setUp(self):
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
        return self.client.post("/v1/bookings", data=json.dumps(body), content_type="application/json", HTTP_X_CLIENT="mobile")

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


class PaymentBypassTests(TestCase):
    def setUp(self):
        provider = Provider.objects.create(id="p1", legal_name="P", display_name="P", adapter_key="x")
        service = Service.objects.create(id="s1", provider=provider, mode="bus", title="KTM-PKR")
        Occurrence.objects.create(
            id="occ_1", service=service, mode="bus", departs_at=timezone.now() + timezone.timedelta(days=1),
            base_price_minor=100000, capacity=5,
        )

    def _book(self, payment, **extra):
        return self.client.post(
            "/v1/bookings",
            data=json.dumps({
                "offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                "payment": payment, "total": {"amount": 105000, "currency": "NPR"},
                "card": {"number": "4242424242424242", "expiry": "12/99", "cvc": "123"},
            }),
            content_type="application/json", **extra,
        )

    def test_mobile_header_does_not_skip_payment(self):
        self.assertEqual(self._book("esewa", HTTP_X_CLIENT="mobile").json()["status"], "pending_payment")
        self.assertEqual(Ticket.objects.count(), 0)

    def test_card_is_rejected_when_demo_payments_are_off(self):
        self.assertEqual(self._book("card").status_code, 400)
        self.assertEqual(Booking.objects.count(), 0)


class AuthTests(TestCase):
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


@override_settings(DEMO_INSTANT_PAYMENT=True)
class PassportTests(TestCase):
    def setUp(self):
        provider = Provider.objects.create(id="p2", legal_name="P", display_name="P", adapter_key="x")
        ktm = Place.objects.create(id="pl_ktm", kind="airport", code="KTM", name="TIA", city="Kathmandu", country="NP", timezone="Asia/Kathmandu")
        pkr = Place.objects.create(id="pl_pkr", kind="airport", code="PKR", name="Pokhara", city="Pokhara", country="NP", timezone="Asia/Kathmandu")
        del_ = Place.objects.create(id="pl_del", kind="airport", code="DEL", name="Indira Gandhi", city="Delhi", country="IN", timezone="Asia/Kolkata")
        departs = timezone.now() + timezone.timedelta(days=10)
        for sid, dest in (("s_intl", del_), ("s_dom", pkr)):
            service = Service.objects.create(id=sid, provider=provider, mode="air", title=sid, origin_place=ktm, dest_place=dest)
            Occurrence.objects.create(id=f"occ_{sid}", service=service, mode="air", departs_at=departs, base_price_minor=100000, capacity=5)
        self.good = {
            "passportNumber": "pa1234567", "nationality": "npl", "birthDate": "1995-01-01",
            "expiryDate": (departs + timezone.timedelta(days=400)).date().isoformat(), "sex": "M",
        }

    def _book(self, occ, passports=None):
        body = {
            "offer": {"occurrenceId": occ}, "names": ["Asha Rai"], "phone": "9800000000",
            "payment": "card", "total": {"amount": 105000, "currency": "NPR"},
        }
        if passports is not None:
            body["passports"] = passports
        return self.client.post("/v1/bookings", data=json.dumps(body), content_type="application/json", HTTP_X_CLIENT="mobile")

    def test_international_requires_passport(self):
        res = self._book("occ_s_intl")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json()["error"]["code"], "passport_required")
        self.assertEqual(Booking.objects.count(), 0)

    def test_domestic_needs_no_passport(self):
        self.assertEqual(self._book("occ_s_dom").status_code, 201)

    def test_short_validity_rejected(self):
        soon = {**self.good, "expiryDate": (timezone.now() + timezone.timedelta(days=30)).date().isoformat()}
        res = self._book("occ_s_intl", [soon])
        self.assertEqual(res.json()["error"]["code"], "passport_invalid")

    def test_valid_passport_stored_masked(self):
        res = self._book("occ_s_intl", [self.good])
        self.assertEqual(res.status_code, 201)
        passenger = Booking.objects.get().passengers.get()
        self.assertEqual(passenger.passport_last4, "4567")
        self.assertNotIn("PA1234567", passenger.passport_hash)
        self.assertEqual(res.json()["passengers"][0]["passport"], {"numberLast4": "4567", "nationality": "NPL"})


@override_settings(STRIPE_SIMULATE=True, STRIPE_SECRET_KEY="", STRIPE_WEBHOOK_SECRET="whsec_test")
class StripeTests(TestCase):
    def setUp(self):
        provider = Provider.objects.create(id="p1", legal_name="P", display_name="P", adapter_key="x")
        service = Service.objects.create(id="s1", provider=provider, mode="bus", title="KTM-PKR")
        Occurrence.objects.create(
            id="occ_1", service=service, mode="bus", departs_at=timezone.now() + timezone.timedelta(days=1),
            base_price_minor=100000, capacity=5,
        )
        res = self.client.post(
            "/v1/bookings",
            data=json.dumps({
                "offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                "payment": "stripe", "total": {"amount": 105000, "currency": "NPR"},
            }),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        self.sid = res.json()["gateway"]["sessionId"]
        self.booking = Booking.objects.get()

    def _post(self, url, **body):
        return self.client.post(url, data=json.dumps(body), content_type="application/json")

    def _sim(self, outcome):
        return self._post("/v1/dev/stripe/simulate", sessionId=self.sid, outcome=outcome)

    def _verify(self):
        return self._post("/v1/payments/verify", provider="stripe", sessionId=self.sid)

    def test_unpaid_session_is_not_confirmed(self):
        self.assertEqual(self._verify().status_code, 402)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "pending_payment")

    def test_success_confirms_and_issues_ticket(self):
        self.assertEqual(self._sim("succeeded").json(), {"next": "return"})
        self.assertEqual(self._verify().status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual((self.booking.status, Ticket.objects.count()), ("confirmed", 1))

    def test_declines_change_nothing(self):
        for outcome in ("card_declined", "insufficient_funds", "3ds_failed"):
            self.assertEqual(self._sim(outcome).status_code, 402)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "pending_payment")

    def test_expired_session_releases_seats(self):
        self._sim("session_expired")
        self.assertEqual(self._verify().status_code, 402)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "cancelled")
        self.assertEqual(Occurrence.objects.get().seats_sold, 0)

    def test_short_payment_rejected(self):
        self._sim("amount_mismatch")
        self.assertEqual(self._verify().status_code, 409)

    def test_paid_late_is_recovered_when_seats_free(self):
        self._sim("paid_late")
        self.assertEqual(self._verify().status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "confirmed")
        self.assertEqual(Occurrence.objects.get().seats_sold, 1)

    def test_webhook_only_settles_without_browser(self):
        self.assertEqual(self._sim("webhook_only").json(), {"next": "home"})
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "confirmed")

    @override_settings(STRIPE_SIMULATE=False)
    def test_simulator_is_off_outside_dev(self):
        self.assertEqual(self._sim("succeeded").status_code, 404)

    def _signed(self, event, stamp=None, secret="whsec_test"):
        import hashlib, hmac, time
        payload = json.dumps(event).encode()
        t = str(stamp or int(time.time()))
        sig = hmac.new(secret.encode(), t.encode() + b"." + payload, hashlib.sha256).hexdigest()
        return self.client.post("/v1/payments/stripe/webhook", data=payload, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={t},v1={sig}")

    def test_webhook_signature_enforced_and_settles(self):
        event = {"type": "checkout.session.completed", "data": {"object": {"id": self.sid, "payment_status": "paid", "amount_total": 105000}}}
        self.assertEqual(self._signed(event, secret="wrong").status_code, 400)
        self.assertEqual(self._signed(event, stamp=1).status_code, 400)  # replayed old timestamp
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "pending_payment")
        self.assertEqual(self._signed(event).status_code, 200)
        self.assertEqual(self._signed(event).status_code, 200)  # duplicate delivery
        self.booking.refresh_from_db()
        self.assertEqual((self.booking.status, Ticket.objects.count()), ("confirmed", 1))


    def _confirm(self):
        self._sim("succeeded")
        self._verify()
        self.booking.refresh_from_db()

    def test_refund_confirmed_booking_frees_seats_and_voids_tickets(self):
        self._confirm()
        self.assertEqual(Occurrence.objects.get().seats_sold, 1)
        res = refund_booking(self.booking)
        self.assertEqual(res.status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "refunded")
        self.assertEqual(Occurrence.objects.get().seats_sold, 0)
        self.assertEqual(Ticket.objects.get().status, "void")
        self.assertEqual(refund_booking(self.booking).status_code, 200)  # second click: still one refund, seats not freed twice
        self.assertEqual(Occurrence.objects.get().seats_sold, 0)

    def test_unpaid_or_redeemed_booking_not_refundable(self):
        self.assertEqual(refund_booking(self.booking).status_code, 409)
        self._confirm()
        Ticket.objects.update(status="redeemed")
        self.assertEqual(refund_booking(self.booking).status_code, 409)

    def test_refund_due_late_payment_is_refundable(self):
        Booking.objects.filter(pk=self.booking.pk).update(status="cancelled", paid_at=timezone.now())
        Occurrence.objects.update(seats_sold=0)  # a released booking already gave its seats back
        self.booking.refresh_from_db()
        self.assertEqual(refund_booking(self.booking).status_code, 200)
        self.assertEqual(Occurrence.objects.get().seats_sold, 0)  # not freed twice

    def test_dashboard_refund_webhook(self):
        self._confirm()
        charge = {"id": "ch_1", "refunded": True, "metadata": {"booking_reference": self.booking.reference}}
        self.assertEqual(self._signed({"type": "charge.refunded", "data": {"object": charge}}).status_code, 200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, "refunded")
        self.assertEqual(Occurrence.objects.get().seats_sold, 0)

    def test_admin_refund_requires_admin(self):
        self._confirm()
        self.assertEqual(self._post(f"/v1/admin/bookings/{self.booking.id}/refund").status_code, 401)

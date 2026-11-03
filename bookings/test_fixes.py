"""Regression tests for holds vs bookings, scan races, late payments, search and rate-limit IPs."""
import base64
import json
from unittest import mock

from django.db import IntegrityError, connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.jwt import issue_token
from accounts.models import User
from accounts.throttle import client_ip
from carts.models import Hold
from catalog.models import InventoryZone, Occurrence, Place, Provider, Service

from . import gateways
from .models import Booking, Passenger, Ticket
from .views import _release, _recover_late_payment


class Base(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(id="p1", legal_name="P", display_name="P", adapter_key="x")
        self.service = Service.objects.create(id="s1", provider=self.provider, mode="bus", title="KTM-PKR")
        self.occ = Occurrence.objects.create(
            id="occ_1", service=self.service, mode="bus", departs_at=timezone.now() + timezone.timedelta(days=1),
            base_price_minor=100000, capacity=2,
        )
        self.zone = InventoryZone.objects.create(id="z1", occurrence=self.occ, code="GA", label="GA", price_minor=100000, capacity=2)
        mk = lambda n: User.objects.create(email=f"{n}@x.co", full_name=n, password_hash="x")
        self.alice, self.bob = mk("alice"), mk("bob")

    def post(self, path, body, user=None, **extra):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {issue_token(user)}"} if user else {}
        return self.client.post(path, json.dumps(body), content_type="application/json", **headers, **extra)

    def book(self, n=1, user=None, payment="esewa"):
        return self.post("/v1/bookings", {
            "offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"] * n, "phone": "9800000000",
            "payment": payment, "total": {"amount": 105000 * n, "currency": "NPR"},
        }, user)


class HoldTests(Base):
    def test_bad_numbers_are_400_not_500(self):
        for body in ({"qty": "abc"}, {"qty": 0}, {"ttlSeconds": -5}, {"qty": True}):
            res = self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z1", **body}, self.alice)
            self.assertEqual(res.status_code, 400, body)

    def test_holds_cannot_exceed_zone_capacity(self):
        hold = lambda u, q: self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z1", "qty": q}, u).status_code
        self.assertEqual(hold(self.alice, 2), 201)
        self.assertEqual(hold(self.bob, 1), 409)

    def test_other_travellers_hold_blocks_booking_but_own_hold_does_not(self):
        Hold.objects.create(user=self.alice, occurrence=self.occ, zone=self.zone, qty=2,
                            expires_at=timezone.now() + timezone.timedelta(minutes=5))
        self.assertEqual(self.book(1, self.bob).status_code, 409)
        res = self.book(2, self.alice)
        self.assertEqual(res.status_code, 201)
        self.assertFalse(Hold.objects.exists())  # used up by the purchase

    def hold(self, user, qty=1):
        return self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z1", "qty": qty}, user)

    def test_new_hold_replaces_the_old_one(self):
        self.assertEqual(self.hold(self.alice).status_code, 201)
        self.assertEqual(self.hold(self.alice).status_code, 201)
        self.assertEqual(Hold.objects.filter(user=self.alice).count(), 1)

    def test_refused_hold_keeps_the_old_one(self):
        self.assertEqual(self.hold(self.alice).status_code, 201)
        self.assertEqual(self.hold(self.alice, qty=5).status_code, 409)  # trip only has 2 seats
        self.assertEqual(Hold.objects.filter(user=self.alice).count(), 1)

    def test_sold_seats_count_against_holds(self):
        Occurrence.objects.filter(pk=self.occ.pk).update(seats_sold=2)
        self.assertEqual(self.hold(self.alice).status_code, 409)

    def test_hold_limit_is_per_traveller_not_per_ip(self):
        for _ in range(20):
            self.assertEqual(self.hold(self.alice).status_code, 201)
        self.assertEqual(self.hold(self.alice).status_code, 429)
        self.assertEqual(self.hold(self.bob).status_code, 201)


    def test_duplicate_or_oversized_seat_lists_are_400(self):
        for labels in (["1A", "1A"], [f"{i}A" for i in range(51)]):
            res = self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z1", "seatLabels": labels}, self.alice)
            self.assertEqual(res.status_code, 400, labels)


class LatePaymentTests(Base):
    def _expired_booking(self):
        res = self.book(1, self.alice)
        self.assertEqual(res.status_code, 201)
        booking = Booking.objects.get()
        _release(booking)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "cancelled")
        return booking

    def test_late_payment_reconfirms_when_seats_remain(self):
        booking = self._expired_booking()
        res = _recover_late_payment(booking)
        self.assertEqual(res.status_code, 200)
        booking.refresh_from_db()
        self.occ.refresh_from_db()
        self.assertEqual((booking.status, self.occ.seats_sold, Ticket.objects.count()), ("confirmed", 1, 1))

    def test_late_payment_is_recorded_when_seats_are_gone(self):
        booking = self._expired_booking()
        Occurrence.objects.filter(pk=self.occ.pk).update(seats_sold=2)
        res = _recover_late_payment(booking)
        self.assertEqual(res.status_code, 409)
        booking.refresh_from_db()
        self.assertEqual(booking.status, "cancelled")
        self.assertIsNotNone(booking.paid_at)  # support finds refunds via cancelled + paid_at


@override_settings(ESEWA_SECRET_KEY="test-secret", ESEWA_PRODUCT_CODE="EPAYTEST", DEMO_INSTANT_PAYMENT=False)
class VerifyPaymentTests(Base):
    def _pending(self):
        self.assertEqual(self.book(1, self.alice).status_code, 201)
        return Booking.objects.get()

    def _callback(self, booking, total="1050.0", sign=True):
        payload = {"transaction_uuid": booking.payment_ref, "total_amount": total, "product_code": "EPAYTEST",
                   "signed_field_names": "transaction_uuid,total_amount,product_code"}
        message = ",".join(f"{n}={payload[n]}" for n in payload["signed_field_names"].split(","))
        payload["signature"] = gateways._esewa_sign(message) if sign else "forged"
        return base64.b64encode(json.dumps(payload).encode()).decode()

    def verify(self, data):
        with mock.patch("bookings.gateways._http_json", return_value={"status": "COMPLETE"}):
            return self.post("/v1/payments/verify", {"provider": "esewa", "data": data})

    def test_forged_signature_confirms_nothing(self):
        booking = self._pending()
        res = self.verify(self._callback(booking, sign=False))
        self.assertEqual(res.status_code, 402)
        booking.refresh_from_db()
        self.assertEqual((booking.status, Ticket.objects.count()), ("pending_payment", 0))

    def test_paid_amount_must_match_total(self):
        booking = self._pending()
        res = self.verify(self._callback(booking, total="10.0"))
        self.assertEqual(res.status_code, 409)
        booking.refresh_from_db()
        self.assertEqual((booking.status, Ticket.objects.count()), ("pending_payment", 0))

    def test_valid_callback_confirms_once(self):
        booking = self._pending()
        data = self._callback(booking)
        self.assertEqual(self.verify(data).status_code, 200)
        self.assertEqual(self.verify(data).status_code, 200)  # a repeat callback issues no second ticket
        booking.refresh_from_db()
        self.assertEqual((booking.status, Ticket.objects.count()), ("confirmed", 1))

    def test_unknown_gateway_ref_is_404(self):
        booking = self._pending()
        data = self._callback(booking)
        Booking.objects.update(payment_ref="other")
        self.assertEqual(self.verify(data).status_code, 404)

    def test_retry_of_a_started_payment_keeps_its_ref(self):
        body = {"offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                "payment": "esewa", "total": {"amount": 105000, "currency": "NPR"}}
        first = self.post("/v1/bookings", body, self.alice, HTTP_IDEMPOTENCY_KEY="k1")
        ref = Booking.objects.get().payment_ref
        again = self.post("/v1/bookings", body, self.alice, HTTP_IDEMPOTENCY_KEY="k1")
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(again.json()["gateway"], first.json()["gateway"])  # back to the same payment
        self.assertEqual(Booking.objects.get().payment_ref, ref)

    def test_bad_token_is_401_not_a_guest_booking(self):
        body = {"offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                "payment": "esewa", "total": {"amount": 105000, "currency": "NPR"}}
        res = self.client.post("/v1/bookings", json.dumps(body), content_type="application/json", HTTP_AUTHORIZATION="Bearer junk")
        self.assertEqual(res.status_code, 401)
        self.assertFalse(Booking.objects.exists())


class ScanTests(Base):
    def test_same_code_admits_only_once(self):
        booking = Booking.objects.create(
            occurrence=self.occ, offer_id="o", mode="bus", title="t", provider_name="p", contact_phone="1",
            payment_method="esewa", subtotal_minor=1, fees_minor=0, total_minor=1, status="confirmed")
        passenger = Passenger.objects.create(booking=booking, full_name="Asha")
        ticket = Ticket.objects.create(booking=booking, passenger=passenger, occurrence=self.occ)
        with mock.patch("accounts.authz.staff_provider_ids", return_value=["p1"]), mock.patch("accounts.authz.can_scan", return_value=True):
            first = self.post("/v1/scan/redeem", {"code": ticket.code}, self.alice).json()
            second = self.post("/v1/scan/redeem", {"code": ticket.code}, self.alice).json()
        self.assertTrue(first["accepted"])
        self.assertEqual((second["accepted"], second["reason"]), (False, "already_redeemed"))
        self.assertIsNone(first["zoneLabel"])  # standard fare

    def test_zone_ticket_shows_its_zone_at_the_gate(self):
        zone = InventoryZone.objects.create(id="z_vip", occurrence=self.occ, code="VIP", label="VIP", price_minor=1, capacity=1)
        booking = Booking.objects.create(
            occurrence=self.occ, zone=zone, offer_id="o", mode="bus", title="t", provider_name="p", contact_phone="1",
            payment_method="esewa", subtotal_minor=1, fees_minor=0, total_minor=1, status="confirmed")
        passenger = Passenger.objects.create(booking=booking, full_name="Asha")
        ticket = Ticket.objects.create(booking=booking, passenger=passenger, occurrence=self.occ)
        with mock.patch("accounts.authz.staff_provider_ids", return_value=["p1"]), mock.patch("accounts.authz.can_scan", return_value=True):
            res = self.post("/v1/scan/redeem", {"code": ticket.code}, self.alice).json()
        self.assertEqual(res["zoneLabel"], "VIP")
        with mock.patch("accounts.authz.staff_provider_ids", return_value=["p1"]), mock.patch("accounts.authz.can_scan", return_value=True):
            history = self.client.get("/v1/scan/history", HTTP_AUTHORIZATION=f"Bearer {issue_token(self.alice)}").json()
        self.assertEqual(history["scans"][0]["zoneLabel"], "VIP")


class SearchTests(Base):
    def search(self, body):
        return self.post("/v1/search", body)

    def test_bad_input_is_400(self):
        for body in ({"legs": ["x"]}, {"legs": [{}], "maxPrice": "abc"}, {"legs": [{}], "modes": "bus"}):
            self.assertEqual(self.search(body).status_code, 400, body)

    def test_place_and_price_filters_run_in_the_database(self):
        ktm = Place.objects.create(id="pl1", kind="city", name="Kathmandu", city="Kathmandu", country="NP", code="KTM")
        pkr = Place.objects.create(id="pl2", kind="city", name="Pokhara", city="Pokhara", country="NP", code="PKR")
        Service.objects.filter(pk="s1").update(origin_place=ktm, dest_place=pkr)
        hit = self.search({"legs": [{"from": "ktm", "to": "pokh"}]}).json()["offers"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(len(self.search({"legs": [{"from": "Pokhara"}]}).json()["offers"]), 0)
        self.assertEqual(len(self.search({"legs": [{}], "maxPrice": 1}).json()["offers"]), 0)

    def test_date_is_cut_in_nepal_time(self):
        # 02:00 on 3 Nov in Nepal is 2 Nov 20:15 UTC: a UTC-based search would file it under the 2nd.
        from datetime import datetime
        from zoneinfo import ZoneInfo

        Occurrence.objects.filter(pk="occ_1").update(departs_at=datetime(2030, 11, 3, 2, 0, tzinfo=ZoneInfo("Asia/Kathmandu")))
        self.assertEqual(len(self.search({"legs": [{"date": "2030-11-03"}]}).json()["offers"]), 1)
        self.assertEqual(len(self.search({"legs": [{"date": "2030-11-02"}]}).json()["offers"]), 0)


class CatalogLoadTests(Base):
    def test_search_query_count_does_not_grow_with_offers(self):
        for i in range(8):
            occ = Occurrence.objects.create(id=f"occ_x{i}", service=self.service, mode="bus", base_price_minor=100000,
                                            departs_at=timezone.now() + timezone.timedelta(days=2 + i), capacity=10)
            InventoryZone.objects.create(id=f"zx{i}a", occurrence=occ, code="A", label="A", price_minor=100000, capacity=5)
            InventoryZone.objects.create(id=f"zx{i}b", occurrence=occ, code="B", label="B", price_minor=120000, capacity=5)
        with CaptureQueriesContext(connection) as ctx:
            res = self.post("/v1/search", {"legs": [{}]})
        self.assertEqual(len(res.json()["offers"]), 9)
        self.assertLess(len(ctx), 12)  # was ~1 + 2 queries per offer

    def test_search_still_counts_live_holds(self):
        Hold.objects.create(user=self.alice, occurrence=self.occ, zone=self.zone, qty=1,
                            expires_at=timezone.now() + timezone.timedelta(minutes=5))
        Hold.objects.create(user=self.bob, occurrence=self.occ, zone=self.zone, qty=1,
                            expires_at=timezone.now() - timezone.timedelta(minutes=5))  # expired: ignored
        zones = self.post("/v1/search", {"legs": [{}]}).json()["offers"][0]["zones"]
        self.assertEqual(zones[0]["seatsAvailable"], 1)

    def test_featured_has_one_per_mode_then_fills(self):
        Occurrence.objects.create(id="occ_air", service=Service.objects.create(id="s2", provider=self.provider, mode="air", title="X"),
                                  mode="air", base_price_minor=1, departs_at=timezone.now() + timezone.timedelta(days=9), capacity=5)
        Occurrence.objects.create(id="occ_bus2", service=self.service, mode="bus", base_price_minor=1,
                                  departs_at=timezone.now() + timezone.timedelta(days=3), capacity=5)
        ids = [o["occurrenceId"] for o in self.client.get("/v1/featured").json()["offers"]]
        self.assertEqual(ids, ["occ_1", "occ_air", "occ_bus2"])  # bus, then air (one per mode), then the filler

    def test_bad_speed_or_heading_is_400_not_500(self):
        for extra in ({"speedKmh": "fast"}, {"heading": [1]}, {"speedKmh": "nan"}, {"heading": "inf"}):
            with mock.patch("accounts.authz.can_track", return_value=True):
                res = self.post("/v1/occurrences/occ_1/tracking", {"lat": 27.7, "lng": 85.3, **extra}, self.alice)
            self.assertEqual(res.status_code, 400, extra)

    def test_places_runs(self):
        self.assertEqual(self.client.get("/v1/places?mode=bus").json()["cities"], [])


class EmailCaseTests(Base):
    def test_login_ignores_email_case_and_no_case_duplicates(self):
        reg = lambda ident: self.post("/v1/auth/register", {"fullName": "Asha Rai", "identifier": ident, "password": "Tr1cky-Pass-92"})
        self.assertEqual(reg("Asha@Mail.com").status_code, 201)
        self.assertEqual(reg("asha@mail.com").status_code, 409)
        res = self.post("/v1/auth/login", {"identifier": "ASHA@mail.com", "password": "Tr1cky-Pass-92"})
        self.assertEqual(res.status_code, 200)


class ClientIpTests(TestCase):
    def ip(self, xff, hops):
        req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR=xff, REMOTE_ADDR="10.0.0.1")
        with override_settings(TRUSTED_PROXY_HOPS=hops):
            return client_ip(req)

    def test_uses_the_address_our_proxy_appended(self):
        self.assertEqual(self.ip("6.6.6.6, 203.0.113.9", 1), "203.0.113.9")  # spoofed first entry ignored
        self.assertEqual(self.ip("203.0.113.9", 0), "10.0.0.1")  # no proxy configured: socket address


class PaymentFailureTests(Base):
    def failed(self, status):
        self.assertEqual(self.book(1, self.alice).status_code, 201)
        booking = Booking.objects.get()
        with mock.patch("bookings.gateways._http_json", return_value={"status": status}):
            res = self.post("/v1/payments/verify", {"provider": "esewa", "failed": 1, "ref": booking.payment_ref})
        self.assertEqual(res.status_code, 402)
        booking.refresh_from_db()
        return booking.status

    def test_unsigned_esewa_failed_cancels_only_when_esewa_agrees(self):
        self.assertEqual(self.failed("PENDING"), "pending_payment")  # a forged "failed" cannot cancel a live payment

    def test_esewa_cancel_frees_the_seats_at_once(self):
        self.assertEqual(self.failed("CANCELED"), "cancelled")
        self.assertEqual(self.book(1, self.alice).status_code, 201)  # the traveller can retry straight away


class ThrottleTests(Base):
    def test_login_limit_counts_every_try(self):
        codes = [self.post("/v1/auth/login", {"identifier": "a@x.co", "password": "wrong-pass"}).status_code for _ in range(6)]
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)


class ZoneBookingTests(Base):
    def setUp(self):
        super().setUp()
        self.vip = InventoryZone.objects.create(id="z_vip", occurrence=self.occ, code="VIP", label="VIP", price_minor=300000, capacity=1)
        Occurrence.objects.filter(pk=self.occ.pk).update(capacity=10)

    def zbook(self, n=1, user=None, zone="z_vip", **extra):
        body = {"offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"] * n, "phone": "9800000000",
                "payment": "esewa", "zoneId": zone, "total": {"amount": 315000 * n, "currency": "NPR"}, **extra}
        return self.post("/v1/bookings", body, user)

    def test_zone_price_is_charged_and_zone_counter_moves(self):
        res = self.zbook(1, self.alice)
        self.assertEqual(res.status_code, 201, res.content)
        self.vip.refresh_from_db()
        self.assertEqual(self.vip.seats_sold, 1)
        self.assertEqual(Booking.objects.get().total_minor, 315000)

    def test_zone_capacity_is_enforced_and_released_with_the_booking(self):
        self.assertEqual(self.zbook(1, self.alice).status_code, 201)
        self.assertEqual(self.zbook(1, self.bob).status_code, 409)  # only 1 VIP place
        _release(Booking.objects.get())
        self.vip.refresh_from_db()
        self.assertEqual(self.vip.seats_sold, 0)
        self.assertEqual(self.zbook(1, self.bob).status_code, 201)

    def test_wrong_zone_price_or_foreign_zone_is_refused(self):
        self.assertEqual(self.zbook(1, self.alice, total={"amount": 105000, "currency": "NPR"}).status_code, 409)
        self.assertEqual(self.zbook(1, self.alice, zone="nope").status_code, 400)

    def test_late_payment_is_refused_when_the_zone_filled_up(self):
        self.assertEqual(self.zbook(1, self.alice).status_code, 201)
        late = Booking.objects.get()
        _release(late)
        self.assertEqual(self.zbook(1, self.bob).status_code, 201)  # takes the only VIP place
        res = _recover_late_payment(late)
        self.assertEqual(res.status_code, 409)
        late.refresh_from_db()
        self.assertEqual(late.status, "cancelled")

    def test_sold_zone_place_blocks_a_hold(self):
        self.assertEqual(self.zbook(1, self.alice).status_code, 201)
        res = self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z_vip", "qty": 1}, self.bob)
        self.assertEqual(res.status_code, 409)

    def test_tickets_name_their_zone(self):
        self.assertEqual(self.zbook(1, self.alice).status_code, 201)
        booking = Booking.objects.get()
        from .views import _issue_tickets
        _issue_tickets(booking)
        self.assertEqual(Ticket.objects.get().to_dict()["zoneLabel"], "VIP")


class SeatMapSweepTests(Base):
    def test_seat_map_frees_seats_of_abandoned_payments(self):
        self.assertEqual(self.book(2, self.alice).status_code, 201)
        Booking.objects.update(created_at=timezone.now() - timezone.timedelta(hours=1))
        res = self.client.get("/v1/occurrences/occ_1/seatmap")
        self.assertEqual(res.status_code, 200)
        self.occ.refresh_from_db()
        self.assertEqual(self.occ.seats_sold, 0)
        self.assertEqual(Booking.objects.get().status, "cancelled")


@override_settings(DEMO_INSTANT_PAYMENT=True)
class IdempotencyTests(Base):
    def send(self, total=105000, instant=True, **extra):
        """instant: the demo path that confirms at once; otherwise the booking waits for the gateway."""
        headers = {"HTTP_X_CLIENT": "mobile"} if instant else {}
        return self.post("/v1/bookings", {
            "offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
            "payment": "esewa", "total": {"amount": total, "currency": "NPR"}, **extra,
        }, self.alice, HTTP_IDEMPOTENCY_KEY="k1", **headers)

    def test_same_key_same_order_replays_the_booking(self):
        self.assertEqual((self.send().status_code, self.send().status_code), (201, 200))
        self.assertEqual(Booking.objects.count(), 1)

    def test_same_key_changed_order_is_409_and_books_nothing(self):
        InventoryZone.objects.create(id="z_vip", occurrence=self.occ, code="VIP", label="VIP", price_minor=300000, capacity=1)
        self.assertEqual(self.send().status_code, 201)
        res = self.send(total=315000, zoneId="z_vip")
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "idempotency_conflict"))
        self.assertEqual(Booking.objects.count(), 1)

    def test_same_key_other_seat_is_409(self):
        InventoryZone.objects.create(id="z_seat", occurrence=self.occ, code="S", label="Seats", price_minor=100000, capacity=2, is_reserved_seating=True)
        send = lambda label: self.send(seats=[label])
        first = send("1A")
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual(send("1A").status_code, 200)
        self.assertEqual(send("1B").status_code, 409)

    def test_same_key_after_the_booking_was_cancelled_starts_a_new_one(self):
        self.assertEqual(self.send(instant=False).status_code, 201)
        _release(Booking.objects.get())  # e.g. the payment was cancelled
        self.assertEqual(self.send(instant=False).status_code, 201)
        self.assertEqual(sorted(Booking.objects.values_list("status", flat=True)), ["cancelled", "pending_payment"])
        self.assertEqual(self.send(instant=False).status_code, 200)  # the new booking is deduped again

    def test_repeat_tap_after_the_gateway_started_returns_the_same_payment(self):
        first = self.send(instant=False)
        again = self.send(instant=False)
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertEqual(again.json()["gateway"], first.json()["gateway"])
        self.assertEqual(Booking.objects.count(), 1)

    def test_started_payment_without_saved_details_is_409_not_a_fake_success(self):
        self.assertEqual(self.send(instant=False).status_code, 201)
        Booking.objects.update(gateway_info=None)  # booked before the details were kept
        res = self.send(instant=False)
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "payment_in_progress"))

    def test_gateway_failure_can_be_retried_with_the_same_key(self):
        with mock.patch("bookings.gateways.esewa_start", side_effect=gateways.GatewayError("down")):
            self.assertEqual(self.send(instant=False).status_code, 502)
        self.assertEqual(self.send(instant=False).status_code, 201)

    def test_a_second_gateway_start_keeps_the_first_payment(self):
        from .views import _start_gateway
        self.assertEqual(self.send(instant=False).status_code, 201)
        booking = Booking.objects.get()
        first_ref, first_info = booking.payment_ref, booking.gateway_info
        Booking.objects.update(payment_ref=None, gateway_info=None)  # a request still starting when the second one began
        winner = _start_gateway(Booking.objects.get())
        loser = _start_gateway(Booking.objects.get())  # started its own payment, but arrives second
        booking.refresh_from_db()
        self.assertEqual(loser, winner)
        self.assertEqual((booking.payment_ref.startswith(booking.reference), booking.gateway_info), (True, winner))
        self.assertNotEqual(booking.payment_ref, first_ref)

    def test_retry_while_the_first_request_is_still_starting_is_409(self):
        from .views import _replay
        self.assertEqual(self.send(instant=False).status_code, 201)
        Booking.objects.update(payment_ref=None, gateway_info=None)
        res = _replay("%s:k1" % self.alice.id, ("occ_1", None, 1, 105000, None), restart=False)
        self.assertEqual(res.status_code, 409)

    def test_an_old_pending_booking_is_not_replayed(self):
        self.assertEqual(self.send(instant=False).status_code, 201)
        Booking.objects.update(created_at=timezone.now() - timezone.timedelta(hours=1))
        self.assertEqual(self.send(instant=False).status_code, 201)  # fresh booking, not the expired one's link
        self.assertEqual(Booking.objects.count(), 2)

    def test_failed_gateway_call_keeps_a_payment_a_parallel_request_started(self):
        from .views import _with_gateway
        self.assertEqual(self.send(instant=False).status_code, 201)
        saved = Booking.objects.get()
        Booking.objects.update(payment_ref=None, gateway_info=None)
        stale = Booking.objects.get()  # this request has not seen the payment the other one starts next
        Booking.objects.update(payment_ref=saved.payment_ref, gateway_info=saved.gateway_info)
        with mock.patch("bookings.gateways.esewa_start", side_effect=gateways.GatewayError("down")):
            res = _with_gateway(stale, 201)
        self.assertEqual(res.status_code, 201)
        self.assertEqual(json.loads(res.content)["gateway"], saved.gateway_info)
        self.assertEqual(Booking.objects.get().status, "pending_payment")


class DepartedTripTests(Base):
    def test_a_trip_that_already_left_cannot_be_booked_or_held(self):
        Occurrence.objects.filter(pk=self.occ.pk).update(departs_at=timezone.now() - timezone.timedelta(hours=2))
        self.assertEqual(self.book(1, self.alice).status_code, 400)
        res = self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z1", "qty": 1}, self.alice)
        self.assertEqual(res.status_code, 400)
        self.assertFalse(Booking.objects.exists() or Hold.objects.exists())


class CartOwnershipTests(Base):
    def test_other_travellers_cannot_read_or_fill_my_cart(self):
        cart_id = self.post("/v1/carts", {}, self.alice).json()["cartId"]
        hold = self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z1", "qty": 1}, self.bob).json()
        get = lambda user: self.client.get(f"/v1/carts/{cart_id}", HTTP_AUTHORIZATION=f"Bearer {issue_token(user)}").status_code
        self.assertEqual((get(self.alice), get(self.bob)), (200, 404))
        res = self.post(f"/v1/carts/{cart_id}/items", {"holdId": hold["holdId"]}, self.bob)
        self.assertEqual(res.status_code, 404)


class RegisterRaceTests(Base):
    def test_losing_the_save_race_is_409_not_500(self):
        body = {"fullName": "Asha Rai", "identifier": "race@mail.com", "password": "Tr1cky-Pass-92"}
        with mock.patch.object(User, "save", side_effect=IntegrityError):
            self.assertEqual(self.post("/v1/auth/register", body).status_code, 409)


class StaffCacheTests(TestCase):
    def test_staff_ids_are_cached_briefly_and_denials_are_not(self):
        from accounts import authz
        authz._staff_cache.clear()
        with mock.patch.object(authz, "_staff_provider_ids", side_effect=[["p1"], [], ["p2"]]) as ask:
            self.assertEqual((authz.staff_provider_ids("u1"), authz.staff_provider_ids("u1")), (["p1"], ["p1"]))
            self.assertEqual((authz.staff_provider_ids("u2"), authz.staff_provider_ids("u2")), ([], ["p2"]))
            self.assertEqual(ask.call_count, 3)
        authz._staff_cache.clear()


class SeedStaysTests(TestCase):
    def test_running_it_again_keeps_nights_and_their_sales(self):
        from django.core.management import call_command
        call_command("seed_stays", verbosity=0)
        night = Occurrence.objects.filter(mode="hotel").order_by("departs_at").first()
        Occurrence.objects.filter(pk=night.pk).update(seats_sold=3)
        count = Occurrence.objects.count()
        call_command("seed_stays", verbosity=0)
        night.refresh_from_db()
        self.assertEqual((Occurrence.objects.count(), night.seats_sold, night.departs_at), (count, 3, night.departs_at))

    def test_ids_fit_their_columns(self):
        from django.core.management import call_command
        call_command("seed_stays", verbosity=0)
        self.assertLessEqual(max(len(i) for i in Occurrence.objects.values_list("id", flat=True)), 32)
        self.assertLessEqual(max(len(i) for i in InventoryZone.objects.values_list("id", flat=True)), 32)


class CartHandoffTests(Base):
    def test_cart_items_name_the_held_zone(self):
        cart_id = self.post("/v1/carts", {}, self.alice).json()["cartId"]
        hold = self.post("/v1/holds", {"occurrenceId": "occ_1", "zoneId": "z1", "qty": 1}, self.alice).json()
        cart = self.post(f"/v1/carts/{cart_id}/items", {"holdId": hold["holdId"]}, self.alice).json()
        self.assertEqual(cart["items"][0]["zoneId"], "z1")


class RevokeAccessTests(TestCase):
    def setUp(self):
        from accounts import authz
        self.authz = authz
        authz._staff_cache.clear()
        Provider.objects.create(id="pa", legal_name="A", display_name="A", adapter_key="x")
        self.user = User.objects.create(email="staff@x.co", full_name="s", password_hash="x", role="gate_agent")

    def test_revoke_deletes_both_staff_relations_and_tolerates_missing_tuples(self):
        calls = []

        def fake_write(writes=(), deletes=()):
            calls.extend(t["relation"] for t in deletes)
            raise self.authz.AuthzError("cannot delete a tuple which does not exist")  # one of the two never existed

        with mock.patch.object(self.authz, "_write", fake_write), \
             mock.patch.object(self.authz, "is_admin", return_value=False), \
             mock.patch.object(self.authz, "_staff_provider_ids", return_value=[]):
            self.authz.revoke_staff(self.user, "pa")
        self.assertEqual(sorted(calls), ["gate_agent", "operator_staff"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "traveller")

    def test_label_stays_while_other_rights_remain(self):
        with mock.patch.object(self.authz, "_write"), \
             mock.patch.object(self.authz, "is_admin", return_value=False), \
             mock.patch.object(self.authz, "_staff_provider_ids", return_value=["pb"]):
            self.authz.revoke_staff(self.user, "pa")
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "gate_agent")

    def test_a_real_failure_is_not_swallowed(self):
        with mock.patch.object(self.authz, "_write", side_effect=self.authz.AuthzError("HTTP 500")):
            with self.assertRaises(self.authz.AuthzError):
                self.authz.revoke_staff(self.user, "pa")


class SeedRealDataTests(TestCase):
    def reseed_queries(self, days):
        from django.core.management import call_command
        call_command("seed_real_data", "--days", str(days), verbosity=0)
        with CaptureQueriesContext(connection) as queries:
            call_command("seed_real_data", "--days", str(days), verbosity=0)
        return len(queries)

    def test_reseeding_adds_nothing_and_costs_no_query_per_trip(self):
        from django.core.management import call_command
        call_command("seed_real_data", "--days", "2", verbosity=0)
        count = Occurrence.objects.count()
        call_command("seed_real_data", "--days", "2", verbosity=0)
        self.assertEqual(Occurrence.objects.count(), count)
        # Fixed catalog upserts only: tripling the days must not triple the queries (it did, one exists() per trip).
        self.assertLess(self.reseed_queries(6), self.reseed_queries(2) + 50)


class SetPasswordCommandTests(Base):
    def test_new_password_works_and_old_tokens_stop(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        user = self.alice
        old_token = issue_token(user)
        call_command("set_password", identifier="alice@x.co", password="Tr1cky-Pass-92", verbosity=0)
        login = self.post("/v1/auth/login", {"identifier": "alice@x.co", "password": "Tr1cky-Pass-92"})
        self.assertEqual(login.status_code, 200)
        me = self.client.get("/v1/auth/me", HTTP_AUTHORIZATION=f"Bearer {old_token}")
        self.assertEqual(me.status_code, 401)
        with self.assertRaises(CommandError):
            call_command("set_password", identifier="alice@x.co", password="short", verbosity=0)


class OfferCapacityTests(Base):
    def test_offers_report_their_capacity_so_the_card_can_show_how_full_they_are(self):
        Occurrence.objects.filter(pk=self.occ.pk).update(seats_sold=1)
        offer = self.client.post("/v1/search", json.dumps({"legs": [{}]}), content_type="application/json").json()["offers"][0]
        self.assertEqual((offer["capacity"], offer["seatsAvailable"]), (2, 1))


class StayClockTests(TestCase):
    def test_check_in_is_14_00_in_kathmandu_and_early_seeds_are_corrected(self):
        from django.core.management import call_command
        call_command("seed_stays", verbosity=0)
        night = Occurrence.objects.filter(mode="hotel").order_by("departs_at").first()
        self.assertEqual(timezone.localtime(night.departs_at).strftime("%H:%M"), "14:00")
        # An early seed stored the same night at 14:00 UTC (7:45 PM in Kathmandu): reseeding moves only the clock time.
        Occurrence.objects.filter(pk=night.pk).update(
            departs_at=timezone.localtime(night.departs_at).replace(hour=19, minute=45), seats_sold=2)
        count = Occurrence.objects.count()
        call_command("seed_stays", verbosity=0)
        night.refresh_from_db()
        self.assertEqual(timezone.localtime(night.departs_at).strftime("%H:%M"), "14:00")
        self.assertEqual((night.seats_sold, Occurrence.objects.count()), (2, count))
        halls = Occurrence.objects.filter(mode="hall").first()
        self.assertEqual(timezone.localtime(halls.departs_at).strftime("%H:%M"), "09:00")


class ReconcileTests(Base):
    """Customers who paid at the gateway but never came back to the site."""

    def pending(self, payment="esewa"):
        started = {"provider": "khalti", "method": "GET", "url": "https://pay.example/k", "pidx": "pidx-1"}
        with mock.patch("bookings.gateways.khalti_start", return_value=started):
            res = self.post("/v1/bookings", {
                "offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                "payment": payment, "total": {"amount": 105000, "currency": "NPR"},
            }, self.alice)
        self.assertEqual(res.status_code, 201, res.content)
        return Booking.objects.get()

    def run_reconcile(self, esewa="COMPLETE", khalti=("Completed", 105000)):
        from .reconcile import reconcile
        with mock.patch("bookings.gateways.esewa_status", return_value=esewa), \
             mock.patch("bookings.gateways.khalti_check", return_value=khalti):
            return reconcile()

    def test_paid_but_never_returned_gets_its_tickets(self):
        booking = self.pending()
        self.assertEqual(self.run_reconcile(), 1)
        booking.refresh_from_db()
        self.assertEqual((booking.status, Ticket.objects.count()), ("confirmed", 1))
        self.assertEqual(self.run_reconcile(), 0)  # confirmed bookings are not chased again

    def test_unpaid_bookings_are_left_alone(self):
        booking = self.pending()
        self.assertEqual(self.run_reconcile(esewa="PENDING"), 0)
        booking.refresh_from_db()
        self.assertEqual((booking.status, Ticket.objects.count()), ("pending_payment", 0))

    def test_paid_after_the_sweep_cancelled_it_is_reconfirmed_when_seats_remain(self):
        booking = self.pending("khalti")
        _release(booking)
        self.assertEqual(self.run_reconcile(), 1)
        booking.refresh_from_db()
        self.assertEqual((booking.status, Ticket.objects.count()), ("confirmed", 1))

    def test_paid_after_the_seats_went_elsewhere_is_marked_refund_due(self):
        booking = self.pending()
        _release(booking)
        self.assertEqual(self.book(2, self.bob).status_code, 201)  # the trip's 2 seats are sold to someone else
        self.run_reconcile()
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.to_dict()["refundDue"]), ("cancelled", True))
        admin = User.objects.create(email="boss@x.co", full_name="Boss", password_hash="x")
        with mock.patch("accounts.authz.is_admin", return_value=True):
            rows = self.client.get("/v1/admin/bookings", HTTP_AUTHORIZATION=f"Bearer {issue_token(admin)}").json()["bookings"]
        self.assertTrue(any(r["refundDue"] for r in rows))

    def test_a_down_gateway_stops_after_three_errors_and_changes_nothing(self):
        from .reconcile import reconcile
        for i in range(5):
            Booking.objects.create(occurrence=self.occ, offer_id="o", mode="bus", title="t", provider_name="p", contact_phone="1",
                                   payment_method="esewa", payment_ref=f"r{i}", subtotal_minor=1, fees_minor=0, total_minor=1,
                                   status="pending_payment")
        with mock.patch("bookings.gateways.esewa_status", side_effect=gateways.GatewayError("down")) as ask:
            self.assertEqual(reconcile(), 0)
        self.assertEqual(ask.call_count, 3)
        self.assertEqual(Booking.objects.filter(status="pending_payment").count(), 5)

    def test_old_abandoned_checkouts_do_not_crowd_out_a_recent_payment(self):
        from .reconcile import reconcile
        paid = self.pending()
        for i in range(40):
            Booking.objects.create(occurrence=self.occ, offer_id="o", mode="bus", title="t", provider_name="p", contact_phone="1",
                                   payment_method="esewa", payment_ref=f"old{i}", subtotal_minor=1, fees_minor=0, total_minor=1,
                                   status="cancelled")
        Booking.objects.exclude(pk=paid.pk).update(created_at=timezone.now() - timezone.timedelta(minutes=90))
        with mock.patch("bookings.gateways.esewa_status", side_effect=lambda ref, total: "COMPLETE" if ref == paid.payment_ref else "PENDING"):
            reconcile(limit=30)
        paid.refresh_from_db()
        self.assertEqual(paid.status, "confirmed")


class LookupTests(Base):
    def test_guest_gets_tickets_with_reference_and_phone_only(self):
        res = self.post("/v1/bookings", {"offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                                         "payment": "esewa", "total": {"amount": 105000, "currency": "NPR"}})
        ref = res.json()["reference"]
        ask = lambda **b: self.post("/v1/bookings/lookup", b)
        self.assertEqual(ask(reference=ref.lower(), phone="98000 00000").status_code, 200)  # case and spacing do not matter
        self.assertEqual(ask(reference=ref, phone="9800000001").status_code, 404)           # wrong phone
        self.assertEqual(ask(reference="SWP-ZZZZZZ", phone="9800000000").status_code, 404)  # no such reference
        self.assertEqual(ask(reference=ref).status_code, 404)

    def test_lookup_is_rate_limited(self):
        codes = [self.post("/v1/bookings/lookup", {"reference": "SWP-AAAAAA", "phone": "9800000000"}).status_code for _ in range(11)]
        self.assertEqual((codes[:10], codes[10]), ([404] * 10, 429))


class ContinuePaymentTests(Base):
    def test_pending_bookings_carry_their_payment_so_the_traveller_can_finish_paying(self):
        res = self.post("/v1/bookings", {"offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                                         "payment": "esewa", "total": {"amount": 105000, "currency": "NPR"}}, self.alice)
        gateway = res.json()["gateway"]
        mine = self.client.get("/v1/bookings/mine", HTTP_AUTHORIZATION=f"Bearer {issue_token(self.alice)}").json()["bookings"][0]
        self.assertEqual(mine["gateway"], gateway)
        found = self.post("/v1/bookings/lookup", {"reference": mine["reference"], "phone": "9800000000"}).json()
        self.assertEqual(found["gateway"], gateway)
        Booking.objects.update(status="confirmed")
        mine = self.client.get("/v1/bookings/mine", HTTP_AUTHORIZATION=f"Bearer {issue_token(self.alice)}").json()["bookings"][0]
        self.assertIsNone(mine["gateway"])


class StandardBookingsCountAgainstTheirZoneTests(Base):
    def setUp(self):
        super().setUp()
        InventoryZone.objects.filter(pk="z1").update(capacity=2)
        InventoryZone.objects.create(id="z_ste", occurrence=self.occ, code="STE", label="Suite", price_minor=300000, capacity=5)
        Occurrence.objects.filter(pk=self.occ.pk).update(capacity=7)  # 2 standard + 5 suites

    def standard(self, user):
        return self.post("/v1/bookings", {"offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                                          "payment": "esewa", "total": {"amount": 105000, "currency": "NPR"}}, user)

    def test_standard_bookings_cannot_exceed_the_standard_zone(self):
        self.assertEqual(self.standard(self.alice).status_code, 201)
        self.assertEqual(self.standard(self.bob).status_code, 201)
        res = self.standard(self.alice)  # the trip still has 5 suites, but no standard place
        self.assertEqual((res.status_code, res.json()["error"]["code"]), (409, "seat_unavailable"))
        self.assertEqual(InventoryZone.objects.get(pk="z1").seats_sold, 2)

    def test_retrying_a_standard_booking_with_the_same_key_still_replays_it(self):
        send = lambda: self.post("/v1/bookings", {"offer": {"occurrenceId": "occ_1"}, "names": ["Asha Rai"], "phone": "9800000000",
                                                  "payment": "esewa", "total": {"amount": 105000, "currency": "NPR"}},
                                 self.alice, HTTP_IDEMPOTENCY_KEY="std-1")
        self.assertEqual(send().status_code, 201)
        self.assertEqual(send().status_code, 200)  # the server-picked zone must not read as "changed order"


class RevokeLabelTests(TestCase):
    def label_after(self, start, admin, staff):
        from accounts import authz
        user = User.objects.create(email=f"{start}{admin}{len(staff)}@x.co", full_name="b", password_hash="x", role=start)
        with mock.patch.object(authz, "is_admin", return_value=admin), mock.patch.object(authz, "_staff_provider_ids", return_value=staff):
            authz._reset_label_if_powerless(user)
        user.refresh_from_db()
        return user.role

    def test_label_follows_the_remaining_rights(self):
        self.assertEqual(self.label_after("admin", False, ["pa"]), "operator_staff")   # lost admin, still staff: not the admin console
        self.assertEqual(self.label_after("gate_agent", False, ["pa"]), "gate_agent")  # a staff label is kept as it is
        self.assertEqual(self.label_after("gate_agent", False, []), "traveller")        # nothing left
        self.assertEqual(self.label_after("admin", True, []), "admin")                  # still admin

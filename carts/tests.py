"""Guests (an anonymous chat session) can hold seats and build a cart without signing in."""
import json

from django.test import TestCase
from django.utils import timezone

from catalog.models import InventoryZone, Occurrence, Provider, Service

KEY = "a" * 32


class GuestCartTests(TestCase):
    def setUp(self):
        provider = Provider.objects.create(id="p1", legal_name="P", display_name="P", adapter_key="x")
        service = Service.objects.create(id="s1", provider=provider, mode="bus", title="KTM-PKR")
        self.occ = Occurrence.objects.create(
            id="occ_1", service=service, mode="bus", departs_at=timezone.now() + timezone.timedelta(days=1),
            base_price_minor=100000, capacity=5,
        )
        self.zone = InventoryZone.objects.create(id="z1", occurrence=self.occ, code="GA", label="GA", price_minor=100000, capacity=5)

    def post(self, path, body, key=KEY):
        return self.client.post("/v1" + path, json.dumps(body), content_type="application/json", **({"HTTP_X_GUEST_KEY": key} if key else {}))

    def test_guest_holds_and_reads_own_cart(self):
        hold = self.post("/holds", {"occurrenceId": "occ_1", "zoneId": "z1", "qty": 1})
        self.assertEqual(hold.status_code, 201)
        cart = self.post("/carts", {}).json()
        added = self.post(f"/carts/{cart['cartId']}/items", {"holdId": hold.json()["holdId"]})
        self.assertEqual(added.status_code, 201)
        self.assertEqual(self.client.get(f"/v1/carts/{cart['cartId']}", HTTP_X_GUEST_KEY=KEY).status_code, 200)

    def test_other_guest_cannot_read_cart(self):
        cart = self.post("/carts", {}).json()
        self.assertEqual(self.client.get(f"/v1/carts/{cart['cartId']}", HTTP_X_GUEST_KEY="b" * 32).status_code, 404)

    def test_no_identity_is_rejected(self):
        self.assertEqual(self.post("/holds", {"occurrenceId": "occ_1", "zoneId": "z1"}, key=None).status_code, 401)
        self.assertEqual(self.post("/holds", {"occurrenceId": "occ_1", "zoneId": "z1"}, key="short").status_code, 401)

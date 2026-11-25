import json

from django.test import TestCase
from django.utils import timezone

from catalog.models import Occurrence, Provider, Service


class SearchHidesDepartedTests(TestCase):
    def setUp(self):
        provider = Provider.objects.create(id="p1", legal_name="P", display_name="P", adapter_key="x")
        service = Service.objects.create(id="s1", provider=provider, mode="event", title="Walk")
        now = timezone.now()
        for oid, delta in (("occ_gone", -1), ("occ_soon", 1)):  # one started an hour ago, one starts in an hour
            Occurrence.objects.create(
                id=oid, service=service, mode="event", departs_at=now + timezone.timedelta(hours=delta), base_price_minor=1000, capacity=5,
            )

    def _ids(self, leg):
        res = self.client.post("/v1/search", data=json.dumps({"legs": [leg]}), content_type="application/json")
        return {o["occurrenceId"] for o in res.json()["offers"]}

    def test_started_events_hidden_with_and_without_date(self):
        today = timezone.localdate(timezone.now() + timezone.timedelta(hours=1)).isoformat()
        for leg in ({}, {"date": today}):
            self.assertNotIn("occ_gone", self._ids(leg))
        self.assertIn("occ_soon", self._ids({}))

import uuid

from django.db import models

from accounts.models import User
from bookings.models import Ticket
from catalog.models import Occurrence


def new_scan_event_id():
    return f"scn_{uuid.uuid4().hex[:20]}"


class ScanEvent(models.Model):
    id = models.CharField(primary_key=True, max_length=32, default=new_scan_event_id, editable=False)
    code = models.CharField(max_length=64)
    ticket = models.ForeignKey(Ticket, null=True, blank=True, on_delete=models.SET_NULL, related_name="scan_events")
    occurrence = models.ForeignKey(
        Occurrence, null=True, blank=True, on_delete=models.SET_NULL, related_name="scan_events"
    )
    gate = models.CharField(max_length=50)
    scanned_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="scan_events")

    accepted = models.BooleanField()
    reason = models.CharField(max_length=30, null=True, blank=True)
    message = models.CharField(max_length=255, null=True, blank=True)
    passenger_name = models.CharField(max_length=255, null=True, blank=True)
    trip_title = models.CharField(max_length=255, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def to_dict(self):
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "message": self.message,
            "passengerName": self.passenger_name,
            "tripTitle": self.trip_title,
            "gate": self.gate,
            "occurrenceId": self.occurrence_id,
            "scannedBy": self.scanned_by.full_name if self.scanned_by else None,
            "decidedAt": self.created_at.isoformat(),
        }

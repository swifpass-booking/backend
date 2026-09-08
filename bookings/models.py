import random
import secrets
import uuid

from django.conf import settings
from django.db import models

from catalog.models import Occurrence

REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I — matches the frontend's QR mock


def new_booking_id():
    return f"bkg_{uuid.uuid4().hex[:20]}"


def new_reference():
    return "SWP-" + "".join(random.choice(REFERENCE_ALPHABET) for _ in range(6))


def new_ticket_id():
    return f"tkt_{uuid.uuid4().hex[:20]}"


def new_ticket_code():
    # Opaque bearer credential encoded in the QR. Online-only: the gate looks
    # this up against the DB rather than verifying a signature on-device —
    # see the Flutter GateValidator for the offline, signed version of this.
    return f"SWP1.{secrets.token_urlsafe(16)}"


class Booking(models.Model):
    STATUS_CHOICES = [
        ("confirmed", "Confirmed"),
        ("cancelled", "Cancelled"),
        ("refunded", "Refunded"),
    ]

    id = models.CharField(primary_key=True, max_length=32, default=new_booking_id, editable=False)
    reference = models.CharField(max_length=12, unique=True, default=new_reference, editable=False)
    user = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="bookings"
    )
    occurrence = models.ForeignKey(
        Occurrence, null=True, blank=True, on_delete=models.SET_NULL, related_name="bookings"
    )

    # Snapshot of the offer at purchase time — kept even if the occurrence changes later.
    offer_id = models.CharField(max_length=64)
    mode = models.CharField(max_length=10)
    title = models.CharField(max_length=255)
    provider_name = models.CharField(max_length=255)
    departs_at = models.DateTimeField(null=True, blank=True)

    contact_phone = models.CharField(max_length=20)
    contact_email = models.CharField(max_length=255, null=True, blank=True)
    payment_method = models.CharField(max_length=20)

    passenger_count = models.PositiveSmallIntegerField(default=1)
    subtotal_minor = models.IntegerField()
    fees_minor = models.IntegerField()
    total_minor = models.IntegerField()
    currency = models.CharField(max_length=3, default="NPR")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="confirmed")
    created_via = models.CharField(max_length=10, default="web")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.reference

    def to_dict(self):
        return {
            "bookingId": self.id,
            "reference": self.reference,
            "status": self.status,
            "offerId": self.offer_id,
            "mode": self.mode,
            "title": self.title,
            "providerName": self.provider_name,
            "departsAt": self.departs_at.isoformat() if self.departs_at else None,
            "contact": {"phoneE164": self.contact_phone, "email": self.contact_email},
            "payment": {"method": self.payment_method},
            "passengers": [p.to_dict() for p in self.passengers.all()],
            "tickets": [t.to_dict() for t in self.tickets.all()],
            "subtotal": {"amount": self.subtotal_minor, "currency": self.currency},
            "fees": {"amount": self.fees_minor, "currency": self.currency},
            "total": {"amount": self.total_minor, "currency": self.currency},
            "createdVia": self.created_via,
            "createdAt": self.created_at.isoformat(),
        }


class Passenger(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="passengers")
    full_name = models.CharField(max_length=255)
    is_lead = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def to_dict(self):
        return {"fullName": self.full_name, "isLead": self.is_lead}

    def __str__(self):
        return self.full_name


class Ticket(models.Model):
    STATUS_CHOICES = [
        ("issued", "Issued"),
        ("redeemed", "Redeemed"),
        ("void", "Void"),
    ]

    id = models.CharField(primary_key=True, max_length=32, default=new_ticket_id, editable=False)
    code = models.CharField(max_length=64, unique=True, default=new_ticket_code, editable=False)
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="tickets")
    passenger = models.OneToOneField(Passenger, on_delete=models.CASCADE, related_name="ticket")
    occurrence = models.ForeignKey(
        Occurrence, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets"
    )
    seat_label = models.CharField(max_length=10, null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default="issued")
    redeemed_at = models.DateTimeField(null=True, blank=True)
    redeemed_gate = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def to_dict(self):
        return {
            "ticketId": self.id,
            "code": self.code,
            "passengerName": self.passenger.full_name,
            "seatLabel": self.seat_label,
            "status": self.status,
        }

    def __str__(self):
        return self.code

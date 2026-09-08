from django.db import models


class Place(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    kind = models.CharField(max_length=20)
    code = models.CharField(max_length=10, null=True, blank=True)
    name = models.CharField(max_length=255)
    name_ne = models.CharField(max_length=255, null=True, blank=True)
    city = models.CharField(max_length=100)
    country = models.CharField(max_length=5)
    timezone = models.CharField(max_length=50)

    def __str__(self):
        return self.name


class Provider(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    legal_name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    modes = models.JSONField(default=list)
    adapter_key = models.CharField(max_length=50)
    is_self_serve = models.BooleanField(default=False)

    def __str__(self):
        return self.display_name


class Service(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    provider = models.ForeignKey(Provider, on_delete=models.CASCADE, related_name="services")
    mode = models.CharField(max_length=10)
    code = models.CharField(max_length=30, null=True, blank=True)
    title = models.CharField(max_length=255)
    origin_place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    dest_place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    venue_place = models.ForeignKey(Place, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    attributes = models.JSONField(default=dict)

    def __str__(self):
        return self.title


class Occurrence(models.Model):
    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("cancelled", "Cancelled"),
        ("completed", "Completed"),
    ]

    id = models.CharField(primary_key=True, max_length=32)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="occurrences")
    mode = models.CharField(max_length=10)
    departs_at = models.DateTimeField()
    arrives_at = models.DateTimeField(null=True, blank=True)
    gates_open_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="scheduled")
    base_price_minor = models.IntegerField()
    capacity = models.IntegerField()
    seats_sold = models.IntegerField(default=0)
    provider_ref = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.service.title} @ {self.departs_at.isoformat()}"


class InventoryZone(models.Model):
    id = models.CharField(primary_key=True, max_length=32)
    occurrence = models.ForeignKey(Occurrence, on_delete=models.CASCADE, related_name="zones")
    code = models.CharField(max_length=20)
    label = models.CharField(max_length=100)
    price_minor = models.IntegerField()
    capacity = models.IntegerField()
    is_reserved_seating = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.occurrence_id}:{self.code}"

from django.contrib import admin

from .models import InventoryZone, Occurrence, Place, Provider, Service


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "kind", "city", "country")
    search_fields = ("name", "city")


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "modes", "is_self_serve")


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "mode", "provider")
    list_filter = ("mode",)


@admin.register(Occurrence)
class OccurrenceAdmin(admin.ModelAdmin):
    list_display = ("id", "service", "mode", "departs_at", "status", "seats_sold", "capacity")
    list_filter = ("mode", "status")


@admin.register(InventoryZone)
class InventoryZoneAdmin(admin.ModelAdmin):
    list_display = ("id", "occurrence", "code", "label", "price_minor")

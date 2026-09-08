from django.contrib import admin

from .models import Booking, Passenger, Ticket


class PassengerInline(admin.TabularInline):
    model = Passenger
    extra = 0


class TicketInline(admin.TabularInline):
    model = Ticket
    extra = 0
    readonly_fields = ("id", "code")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("reference", "title", "mode", "status", "total_minor", "created_at")
    list_filter = ("mode", "status", "payment_method")
    search_fields = ("reference", "title", "contact_phone", "contact_email")
    inlines = [PassengerInline, TicketInline]


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "booking", "passenger", "status", "redeemed_at", "redeemed_gate")
    list_filter = ("status",)
    search_fields = ("code", "booking__reference")

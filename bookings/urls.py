from django.urls import path

from . import views

urlpatterns = [
    path("payments/verify", views.verify_payment, name="payments-verify"),
    path("bookings/mine", views.my_bookings, name="bookings-mine"),
    path("bookings/lookup", views.lookup_booking, name="bookings-lookup"),
    path("bookings", views.create_booking, name="bookings-create"),
]

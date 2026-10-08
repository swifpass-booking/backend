from django.urls import path

from . import views

urlpatterns = [
    path("payments/verify", views.verify_payment, name="payments-verify"),
    path("bookings/mine", views.my_bookings, name="bookings-mine"),
    path("bookings", views.create_booking, name="bookings-create"),
]

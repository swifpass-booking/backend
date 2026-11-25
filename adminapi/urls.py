from django.urls import path

from . import views

urlpatterns = [
    path("admin/stats", views.stats, name="admin-stats"),
    path("admin/users", views.users, name="admin-users"),
    path("admin/occurrences", views.occurrences, name="admin-occurrences"),
    path("admin/bookings", views.bookings, name="admin-bookings"),
    path("admin/bookings/<str:booking_id>/refund", views.refund, name="admin-booking-refund"),
]

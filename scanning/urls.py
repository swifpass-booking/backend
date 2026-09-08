from django.urls import path

from . import views

urlpatterns = [
    path("scan/redeem", views.redeem, name="scan-redeem"),
    path("scan/stats", views.stats, name="scan-stats"),
]

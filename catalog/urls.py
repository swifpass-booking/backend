from django.urls import path

from . import views

urlpatterns = [
    path("places", views.places, name="catalog-places"),
    path("featured", views.featured, name="catalog-featured"),
    path("search", views.search, name="catalog-search"),
    path("occurrences/<str:occurrence_id>/tracking", views.tracking, name="catalog-tracking"),
    path("occurrences/<str:occurrence_id>/seatmap", views.seatmap, name="catalog-seatmap"),
]

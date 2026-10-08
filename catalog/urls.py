from django.urls import path

from . import views

urlpatterns = [
    path("search", views.search, name="catalog-search"),
    path("occurrences/<str:occurrence_id>/seatmap", views.seatmap, name="catalog-seatmap"),
]

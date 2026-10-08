from django.urls import path

from . import views

urlpatterns = [
    path("holds", views.create_hold, name="holds-create"),
    path("carts", views.create_cart, name="carts-create"),
    path("carts/<str:cart_id>", views.get_cart, name="carts-get"),
    path("carts/<str:cart_id>/items", views.add_cart_item, name="carts-add-item"),
]

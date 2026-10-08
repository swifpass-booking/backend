from django.urls import path

from . import views

urlpatterns = [
    path("auth/register", views.register, name="auth-register"),
    path("auth/login", views.login, name="auth-login"),
    path("auth/logout", views.logout, name="auth-logout"),
    path("auth/me", views.me, name="auth-me"),
    path("preferences", views.preferences, name="preferences"),
]

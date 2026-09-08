from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("id", "full_name", "email", "phone_e164", "role", "created_at")
    search_fields = ("full_name", "email", "phone_e164")

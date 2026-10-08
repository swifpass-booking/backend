from django.contrib import admin

from .models import Cart, CartItem, Hold


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0


@admin.register(Hold)
class HoldAdmin(admin.ModelAdmin):
    list_display = ("id", "occurrence", "zone", "qty", "expires_at")
    list_filter = ("zone",)


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at", "expires_at")
    inlines = [CartItemInline]

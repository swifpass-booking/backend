import uuid

from django.contrib.auth.hashers import check_password, make_password
from django.db import models


def new_user_id():
    return f"usr_{uuid.uuid4().hex[:20]}"


class User(models.Model):
    """Mirrors the `users` table in docs/schema.sql — the one slice of the
    (otherwise unbuilt) backend this prototype needs: auth."""

    ROLE_CHOICES = [
        ("traveller", "Traveller"),
        ("admin", "Admin"),
    ]

    id = models.CharField(primary_key=True, max_length=32, default=new_user_id, editable=False)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone_e164 = models.CharField(unique=True, null=True, blank=True, max_length=20)
    password_hash = models.CharField(max_length=255)
    full_name = models.CharField(max_length=255)
    locale = models.CharField(max_length=10, default="en-NP")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="traveller")
    created_at = models.DateTimeField(auto_now_add=True)

    def set_password(self, raw_password):
        self.password_hash = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password_hash)

    def to_public_dict(self):
        return {
            "id": self.id,
            "fullName": self.full_name,
            "email": self.email,
            "phoneE164": self.phone_e164,
            "locale": self.locale,
            "role": self.role,
        }

    def __str__(self):
        return self.full_name

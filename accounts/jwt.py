from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings

TOKEN_TTL = timedelta(days=1)


def issue_token(user):
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "ver": user.token_version, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_user(token):
    """Returns the User, or None if the token is invalid, expired,
    or revoked (user's token_version moved past the token's `ver`)."""
    from .models import User

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    user = User.objects.filter(id=sub).first()
    return user if user and payload.get("ver") == user.token_version else None


def decode_token(token):
    """The user id behind a valid token, or None."""
    user = decode_user(token)
    return user.id if user else None

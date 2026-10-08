from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings

TOKEN_TTL = timedelta(days=1)


def issue_token(user):
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "ver": user.token_version, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token):
    """Returns the user id (`sub`), or None if the token is invalid, expired,
    or revoked (user's token_version moved past the token's `ver`)."""
    from .models import User

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    current = User.objects.filter(id=sub).values_list("token_version", flat=True).first()
    if current is None or payload.get("ver") != current:
        return None
    return sub

from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings

TOKEN_TTL = timedelta(days=7)


def issue_token(user_id):
    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_token(token):
    """Returns the user id (`sub`), or None if the token is missing/invalid/expired."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None

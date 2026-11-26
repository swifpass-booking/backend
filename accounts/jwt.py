from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings

TOKEN_TTL = timedelta(days=1)


COOKIE_NAME = "swiftpass_session"
CSRF_HEADER = "X-Requested-With"  # browsers only send this cross-site after a CORS preflight, which we never allow
CSRF_VALUE = "swiftpass"
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def issue_token(user, ttl=TOKEN_TTL):
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "ver": user.token_version, "iat": now, "exp": now + ttl}
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


def request_token(request):
    """The caller's JWT and whether it came from the session cookie. A Bearer header wins (mobile app, voice agent).
    The httpOnly cookie is used by the web app; it only counts on state-changing requests that also carry the
    CSRF header, so another site cannot ride on it."""
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[7:], False
    cookie = request.COOKIES.get(COOKIE_NAME)
    if cookie and (request.method in SAFE_METHODS or request.headers.get(CSRF_HEADER) == CSRF_VALUE):
        return cookie, True
    return None, False


def set_session_cookie(response, token):
    response.set_cookie(
        COOKIE_NAME, token, max_age=int(TOKEN_TTL.total_seconds()),
        httponly=True, secure=not settings.DEBUG, samesite="Lax", path="/",
    )
    return response


def clear_session_cookie(response):
    response.delete_cookie(COOKIE_NAME, path="/", samesite="Lax")
    return response

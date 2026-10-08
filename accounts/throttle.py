import functools
import time

from django.core.cache import cache
from django.http import JsonResponse


def client_ip(request):
    # REMOTE_ADDR only: X-Forwarded-For is spoofable unless a trusted proxy
    # overwrites it. Behind a proxy, configure that at the proxy / WSGI layer.
    return request.META.get("REMOTE_ADDR", "unknown")


def throttle(scope, limit, window, key_func=None):
    """Fixed-window rate limit per client IP (plus key_func(request) if given).
    Uses Django's cache; swap CACHES to a shared backend for multi-process deploys."""

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            parts = [scope, client_ip(request)]
            if key_func:
                extra = key_func(request)
                if extra:
                    parts.append(extra)
            bucket = int(time.time() // window)
            key = "throttle:" + ":".join(parts) + f":{bucket}"
            cache.add(key, 0, timeout=window)
            try:
                count = cache.incr(key)
            except ValueError:
                count = 1
            if count > limit:
                resp = JsonResponse(
                    {"error": {"code": "rate_limited", "message": "Too many attempts. Try again later."}},
                    status=429,
                )
                resp["Retry-After"] = str(window)
                return resp
            return view(request, *args, **kwargs)

        return wrapped

    return decorator

import functools
import random
import time

from django.conf import settings
from django.db.models import F
from django.http import JsonResponse
from django.utils import timezone

from .models import RateCounter


def client_ip(request):
    # X-Forwarded-For is spoofable, so trust only the entry our own proxies appended:
    # with N trusted hops that is the Nth from the right. N=0 (default) means no proxy.
    hops = settings.TRUSTED_PROXY_HOPS
    if hops:
        parts = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.META.get("REMOTE_ADDR", "unknown")


def throttle(scope, limit, window, key_func=None):
    """Fixed-window rate limit per client IP (plus key_func(request) if given).
    Counters live in the database, so every worker shares them and each bump is one atomic UPDATE."""

    def decorator(view):
        @functools.wraps(view)
        def wrapped(request, *args, **kwargs):
            parts = [scope, client_ip(request)]
            if key_func:
                extra = key_func(request)
                if extra:
                    parts.append(extra)
            bucket = int(time.time() // window)
            key = ("throttle:" + ":".join(parts) + f":{bucket}")[:255]
            now = timezone.now()
            RateCounter.objects.get_or_create(key=key, defaults={"expires_at": now + timezone.timedelta(seconds=window)})
            RateCounter.objects.filter(key=key).update(count=F("count") + 1)
            count = RateCounter.objects.values_list("count", flat=True).get(key=key)
            if random.random() < 0.02:  # drop dead windows now and then so the table stays small
                RateCounter.objects.filter(expires_at__lt=now).delete()
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

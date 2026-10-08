"""Road routes via the public OSRM demo server (https://router.project-osrm.org).

Fine for development and demos; for production run your own OSRM or use a paid routing API
(set OSRM_BASE_URL). Routes are cached in the database, so each origin/destination pair is fetched once.
"""
import json
import os
import time
import urllib.error
import urllib.request

from . import geo
from .models import RouteCache

OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
_failed_until = {}  # key -> monotonic time before which we don't retry a failed fetch


def _key(o, d):
    return f"{o[0]:.4f},{o[1]:.4f}>{d[0]:.4f},{d[1]:.4f}"


def road_route(origin, dest):
    """Return {"points": [[lat, lng], ...], "distance_km", "duration_min"} or None if unavailable."""
    key = _key(origin, dest)
    cached = RouteCache.objects.filter(key=key).first()
    if cached:
        return {"points": cached.geometry, "distance_km": cached.distance_km, "duration_min": cached.duration_min}
    if _failed_until.get(key, 0) > time.monotonic():
        return None
    url = (
        f"{OSRM_BASE_URL}/route/v1/driving/{origin[1]},{origin[0]};{dest[1]},{dest[0]}"
        "?overview=full&geometries=geojson"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        route = data["routes"][0]
        points = geo.resample([(lat, lng) for lng, lat in route["geometry"]["coordinates"]], 300)
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError):
        _failed_until[key] = time.monotonic() + 120  # back off for 2 minutes
        return None
    out = {"points": points, "distance_km": route["distance"] / 1000, "duration_min": route["duration"] / 60}
    RouteCache.objects.update_or_create(
        key=key, defaults={"geometry": points, "distance_km": out["distance_km"], "duration_min": out["duration_min"]}
    )
    return out
